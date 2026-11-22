from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from calbase.config import AppPaths, ensure_app_dir, read_config, write_config
from calbase.dayfile import Event

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]


@dataclass(frozen=True)
class CalendarInfo:
    calendar_id: str
    summary: str
    access_role: str


def _load_credentials(paths: AppPaths) -> Optional[Credentials]:
    if not paths.token_path.exists():
        return None
    return Credentials.from_authorized_user_file(paths.token_path, SCOPES)


def _save_credentials(paths: AppPaths, creds: Credentials) -> None:
    ensure_app_dir(paths)
    paths.token_path.write_text(creds.to_json(), encoding="utf-8")


def authenticate(paths: AppPaths, credentials_path: Path) -> Credentials:
    creds = _load_credentials(paths)
    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(paths, creds)
            return creds
        except RefreshError:
            # Fall through to re-authenticate if the refresh token is revoked/expired.
            creds = None

    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Missing Google OAuth credentials at {credentials_path}.\n"
            "Download a Desktop OAuth client JSON from Google Cloud Console and save it there,\n"
            "or set CALBASE_CREDENTIALS=/path/to/credentials.json."
        )

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    try:
        creds = flow.run_local_server(port=0)
    except Exception:
        # Fall back to console-based flow (prints a URL to paste).
        creds = flow.run_console()
    _save_credentials(paths, creds)
    return creds


def build_calendar_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds)


def list_calendars(service) -> list[CalendarInfo]:
    calendars = service.calendarList().list().execute().get("items", [])
    return [
        CalendarInfo(
            calendar_id=item["id"],
            summary=item.get("summary", ""),
            access_role=item.get("accessRole", ""),
        )
        for item in calendars
    ]


def resolve_calendar_id(
    service,
    paths: AppPaths,
    override: Optional[str],
    picker: Optional[Callable[[list[CalendarInfo], Optional[str]], CalendarInfo]] = None,
) -> str:
    """Resolve which calendar to use.

    `picker(writable_calendars, stale_id_or_None) -> CalendarInfo` is called
    when there is no usable cached default. If `picker` is None, we fall back
    to auto-picking the first owned calendar with a stderr notice.
    """
    calendars = list_calendars(service)
    if not calendars:
        raise RuntimeError("No calendars available for this account.")

    if override:
        for calendar in calendars:
            if calendar.calendar_id == override or calendar.summary == override:
                if calendar.access_role not in {"writer", "owner"}:
                    raise ValueError(
                        f'Calendar "{override}" is read-only. '
                        "Choose a calendar with writer access."
                    )
                return calendar.calendar_id
        raise ValueError(f'Calendar "{override}" not found.')

    config = read_config(paths)
    default_id = config.get("default_calendar_id")
    if default_id:
        for calendar in calendars:
            if calendar.calendar_id == default_id:
                if calendar.access_role in {"writer", "owner"}:
                    return default_id
                # Cached default is now read-only; fall through to repick.
                break
        stale_id: Optional[str] = default_id
    else:
        stale_id = None

    writable = [c for c in calendars if c.access_role in {"writer", "owner"}]
    if not writable:
        raise RuntimeError(
            "No writable calendars available. Choose a calendar with --calendar."
        )

    if picker is not None:
        chosen = picker(writable, stale_id)
        if chosen.access_role not in {"writer", "owner"}:
            raise ValueError(f'Calendar "{chosen.summary}" is read-only.')
    else:
        chosen = next(
            (c for c in writable if c.access_role == "owner"),
            writable[0],
        )
        if stale_id:
            sys.stderr.write(
                f'Note: cached default calendar "{stale_id}" is no longer '
                f'accessible; switching to "{chosen.summary}". '
                "Run `cb cals` to see all calendars, or pass `--calendar X`.\n"
            )

    config["default_calendar_id"] = chosen.calendar_id
    write_config(paths, config)
    return chosen.calendar_id


def list_events(
    service,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
) -> list[Event]:
    """Fetch events from Google Calendar in the given time window.

    Recurring events are expanded into singleEvents and sorted by start time.
    """
    items = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
        .get("items", [])
    )
    events: list[Event] = []
    for item in items:
        ev = _event_from_api(item)
        if ev is not None:
            events.append(ev)
    return events


def _event_from_api(item: dict) -> Optional[Event]:
    start_data = item.get("start") or {}
    end_data = item.get("end") or {}
    start_str = start_data.get("dateTime")
    end_str = end_data.get("dateTime")
    # Skip all-day events (date-only) for now; dayfile is time-block oriented.
    if not start_str or not end_str:
        return None

    try:
        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str)
    except ValueError:
        return None

    attendees = tuple(
        a.get("email")
        for a in item.get("attendees", [])
        if a.get("email")
    )
    has_meet = bool(item.get("hangoutLink") or item.get("conferenceData"))

    return Event(
        id=item.get("id", ""),
        title=item.get("summary", ""),
        start=start_dt,
        end=end_dt,
        location=item.get("location"),
        attendees=attendees,
        meet=has_meet,
        description=item.get("description"),
    )


def update_event(
    service,
    calendar_id: str,
    event_id: str,
    body: dict,
    *,
    conference_data_version: Optional[int] = None,
) -> dict:
    """Patch an event with the given partial body."""
    kwargs: dict = {"calendarId": calendar_id, "eventId": event_id, "body": body}
    if conference_data_version is not None:
        kwargs["conferenceDataVersion"] = conference_data_version
    return service.events().patch(**kwargs).execute()


def delete_event(service, calendar_id: str, event_id: str) -> None:
    """Delete an event by id."""
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()

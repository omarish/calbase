from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Optional

import typer

from calbase import __version__
from calbase.config import get_app_paths, read_config, write_config

# Heavy imports (`googleapiclient`, `google_auth_oauthlib`, `dateparser`,
# `dayfile` chain) are deferred to the functions that actually need them so that
# `cb --help` and argv dispatch stay snappy. See `_lazy_*` helpers below.

if TYPE_CHECKING:
    from calbase.dayfile import Event, ParsedLine, Plan

app = typer.Typer(
    name="calbase",
    help=(
        "Like git rebase -i, for your Google Calendar.\n\n"
        "Bulk edit:      cb edit <when>          e.g. `cb edit today`, `cb edit week`\n"
        "Quick capture:  cb <when> <title…>      e.g. `cb 4h Office cleanup`\n"
        "List calendars: cb cals"
    ),
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


# Shared helpers ---------------------------------------------------------


def _lazy_google():
    """Import google_calendar lazily (pulls in googleapiclient, google-auth)."""
    from calbase import google_calendar as gc

    return gc


def _lazy_time_utils():
    """Import time_utils lazily (pulls in dateparser)."""
    from calbase import time_utils as tu

    return tu


def _lazy_dayfile():
    """Import dayfile lazily; cheap on its own but keep symmetry."""
    from calbase import dayfile as df

    return df


def _resolve_credentials_path() -> Path:
    env_path = os.environ.get("CALBASE_CREDENTIALS")
    if env_path:
        return Path(env_path).expanduser()
    return get_app_paths().credentials_path


def _format_event_time(dt: datetime) -> dict:
    """Build a Google Calendar `start`/`end` payload.

    Google requires `timeZone` be an IANA name like "America/Los_Angeles".
    We send it only when we can derive one (i.e. the tzinfo is a `ZoneInfo`);
    otherwise we omit it and Google reads the offset embedded in `dateTime`.
    """
    payload: dict = {"dateTime": dt.isoformat()}
    tz_name = _iana_tz_name(dt.tzinfo)
    if tz_name:
        payload["timeZone"] = tz_name
    return payload


def _iana_tz_name(tzinfo) -> Optional[str]:
    if tzinfo is None:
        return "UTC"
    try:
        from zoneinfo import ZoneInfo

        if isinstance(tzinfo, ZoneInfo):
            return tzinfo.key
    except ImportError:
        pass
    return None


def _local_zone():
    """Best-effort local timezone as a `ZoneInfo` (so it has an IANA `.key`).

    Falls back to the system fixed-offset tzinfo if we can't resolve a name —
    in that case, we'll just omit `timeZone` from the API payload.
    """
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError:
        return datetime.now().astimezone().tzinfo

    candidates: list[str] = []
    tz_env = os.environ.get("TZ")
    if tz_env and "/" in tz_env:
        candidates.append(tz_env)

    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        target = None
    if target:
        idx = target.rfind("zoneinfo/")
        if idx >= 0:
            candidates.append(target[idx + len("zoneinfo/") :])

    for name in candidates:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            continue

    return datetime.now().astimezone().tzinfo


def _edit_text(text: str, *, suffix: str = ".calbase") -> Optional[str]:
    """Open `text` in $VISUAL / $EDITOR (fallback: vi).

    Returns the saved text, or None if the user quit without saving or the
    editor exited with a non-zero status.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    cmd = shlex.split(editor)

    fd, tmp_path = tempfile.mkstemp(prefix="calbase-", suffix=suffix)
    path = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        before_stat = path.stat()
        result = subprocess.run([*cmd, str(path)], check=False)
        if result.returncode != 0:
            return None
        after_stat = path.stat()
        if (
            after_stat.st_mtime_ns == before_stat.st_mtime_ns
            and after_stat.st_size == before_stat.st_size
        ):
            return None
        return path.read_text(encoding="utf-8")
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _get_description_from_editor(initial_content: Optional[str] = None) -> Optional[str]:
    marker = (
        "# Enter the meeting description here\n"
        "# Lines starting with # will be ignored\n"
    )
    text = initial_content if initial_content is not None else marker
    edited = _edit_text(text, suffix=".md")
    if edited is None:
        return None
    body = "\n".join(
        line for line in edited.splitlines() if not line.strip().startswith("#")
    ).strip()
    return body or None


def _get_service():
    """Authenticate and return a (paths, service) tuple."""
    gc = _lazy_google()
    paths = get_app_paths()
    try:
        creds = gc.authenticate(paths, _resolve_credentials_path())
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    return paths, gc.build_calendar_service(creds)


def _interactive_pick_calendar(writable, stale_id):
    """Prompt the user to pick a default calendar from a writable list.

    Falls back to auto-pick if stdin/stdout is not a tty (so non-interactive
    runs like cron jobs or piped input don't hang).
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        chosen = next(
            (c for c in writable if c.access_role == "owner"), writable[0]
        )
        sys.stderr.write(
            f'Note: not a tty; auto-picked "{chosen.summary}" as default.\n'
        )
        return chosen

    if stale_id:
        typer.echo(
            f'\nCached default calendar "{stale_id}" is no longer accessible. '
            "Pick a new default:",
            err=True,
        )
    else:
        typer.echo("\nNo default calendar set. Pick one:", err=True)

    for i, cal in enumerate(writable, start=1):
        typer.echo(
            f"  {i}. {cal.summary}  ({cal.access_role})  -  {cal.calendar_id}",
            err=True,
        )
    while True:
        raw = typer.prompt(f"\nChoice [1-{len(writable)}]")
        try:
            idx = int(raw.strip())
        except ValueError:
            typer.echo(f"Invalid choice {raw!r}. Try again.", err=True)
            continue
        if 1 <= idx <= len(writable):
            chosen = writable[idx - 1]
            typer.echo(
                f'Saved "{chosen.summary}" as the default. '
                "Override per-invocation with `--calendar X`.",
                err=True,
            )
            return chosen
        typer.echo(
            f"Choice {idx} is out of range. Try 1-{len(writable)}.", err=True
        )


def _build_event_body(
    *,
    title: str,
    start: datetime,
    end: datetime,
    location: Optional[str] = None,
    description: Optional[str] = None,
    invite: Optional[list[str]] = None,
    meet: bool = False,
) -> dict:
    body: dict = {
        "summary": title,
        "start": _format_event_time(start),
        "end": _format_event_time(end),
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if invite:
        attendees: list[dict] = []
        for inv in invite:
            for email in inv.split(","):
                email = email.strip()
                if email:
                    attendees.append({"email": email})
        if attendees:
            body["attendees"] = attendees
    if meet:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    return body


def _extract_postfix_modifiers(
    title_tokens: list[str],
    *,
    location: Optional[str],
    invite: Optional[list[str]],
    meet: bool,
) -> tuple[str, Optional[str], list[str], bool]:
    """Pull `@location`, `+meet`, `+invite:email` out of title tokens.

    Explicit flag values take precedence; postfix tokens are merged in.
    """
    invites = list(invite or [])
    title_parts: list[str] = []
    quoted_loc_buffer: Optional[list[str]] = None

    for tok in title_tokens:
        # Multi-word quoted location handling: `@"office park"` or split tokens
        if quoted_loc_buffer is not None:
            quoted_loc_buffer.append(tok)
            if tok.endswith('"'):
                merged = " ".join(quoted_loc_buffer)
                if location is None:
                    location = merged.strip('"')
                quoted_loc_buffer = None
            continue

        if tok.startswith('@"') and not tok.endswith('"'):
            quoted_loc_buffer = [tok]
            continue
        if tok.startswith('@"') and tok.endswith('"') and len(tok) > 3:
            if location is None:
                location = tok[2:-1]
            continue
        if tok.startswith("@") and len(tok) > 1:
            if location is None:
                location = tok[1:]
            continue
        if tok.lower() == "+meet":
            meet = True
            continue
        if tok.lower().startswith("+invite:") and len(tok) > len("+invite:"):
            invites.append(tok[len("+invite:") :])
            continue

        title_parts.append(tok)

    title = " ".join(title_parts).strip()
    return title, location, invites, meet


# Capture flow (default callback) ----------------------------------------


def _do_capture(
    args: list[str],
    *,
    location: Optional[str],
    calendar: Optional[str],
    invite: Optional[list[str]],
    meet: bool,
    description: Optional[str],
    edit_description: bool,
) -> None:
    tu = _lazy_time_utils()
    now = datetime.now(_local_zone())
    try:
        time_range, title_tokens = tu.parse_when(args, now)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        typer.echo(
            "Examples:\n"
            "  cb 4h Office cleanup\n"
            "  cb 9a-12p Deep work\n"
            "  cb sat 8p-11p Server maintenance\n"
            "  cb tomorrow 9-10a 1:1 with Alex",
            err=True,
        )
        raise typer.Exit(code=1)

    title, location, invites, meet = _extract_postfix_modifiers(
        title_tokens, location=location, invite=invite, meet=meet
    )

    if not title:
        typer.echo("Error: missing title.", err=True)
        raise typer.Exit(code=1)

    final_description: Optional[str] = None
    if edit_description:
        final_description = _get_description_from_editor(description)
    elif description:
        final_description = description

    paths, service = _get_service()
    gc = _lazy_google()
    calendar_id = gc.resolve_calendar_id(
        service, paths, calendar, picker=_interactive_pick_calendar
    )

    body = _build_event_body(
        title=title,
        start=time_range.start,
        end=time_range.end,
        location=location,
        description=final_description,
        invite=invites,
        meet=meet,
    )

    created = (
        service.events()
        .insert(calendarId=calendar_id, body=body, conferenceDataVersion=1)
        .execute()
    )
    url = created.get("htmlLink")
    if url:
        typer.echo(f"Event created: {url}")
    else:
        typer.echo("Event created.")
    hangout = created.get("hangoutLink")
    if hangout:
        typer.echo(f"Meet URL: {hangout}")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"calbase {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
) -> None:
    """Bulk edit: cb edit <when>  |  Quick capture: cb <when> <title…>"""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command(
    name="capture",
    hidden=True,
    help="Internal: capture <when> <title…>. Normally invoked implicitly.",
)
def capture_command(
    args: Annotated[
        list[str],
        typer.Argument(
            metavar="WHEN TITLE...",
            help="Time spec then title (e.g. `4h Office cleanup`).",
        ),
    ],
    location: Annotated[
        Optional[str], typer.Option("--location", "-l", help="Event location")
    ] = None,
    calendar: Annotated[
        Optional[str],
        typer.Option("--calendar", help="Calendar id or name (default: configured)"),
    ] = None,
    invite: Annotated[
        Optional[list[str]],
        typer.Option("--invite", "-i", help="Invite an email (repeatable)"),
    ] = None,
    meet: Annotated[
        bool, typer.Option("--meet", "-m", help="Add Google Meet link")
    ] = False,
    description: Annotated[
        Optional[str], typer.Option("--description", help="Event description")
    ] = None,
    edit_description: Annotated[
        bool,
        typer.Option(
            "--edit-description", "-e", help="Open $EDITOR to write description"
        ),
    ] = False,
) -> None:
    _do_capture(
        list(args),
        location=location,
        calendar=calendar,
        invite=invite,
        meet=meet,
        description=description,
        edit_description=edit_description,
    )


# Edit flow --------------------------------------------------------------


def _editor_session(
    initial_text: str,
    anchor_day: date,
    tz,
    *,
    edit_fn=None,
    confirm_fn=None,
) -> tuple[Optional[list["ParsedLine"]], str]:
    """Open $EDITOR, parse on save, retry on parse errors.

    Returns `(parsed_lines, last_text)`. `parsed_lines` is None if the user
    aborted (closed editor without saving, or declined the retry).
    """
    df = _lazy_dayfile()
    edit_fn = edit_fn or _edit_text
    confirm_fn = confirm_fn or typer.confirm

    text = initial_text
    while True:
        edited = edit_fn(text)
        if edited is None:
            return None, text
        # Drop any prior `# !! ERROR` annotations before re-parsing.
        cleaned = "\n".join(
            line for line in edited.splitlines() if not line.startswith("# !! ERROR")
        )
        text = cleaned if cleaned.endswith("\n") else cleaned + "\n"

        parsed, errors = df.parse_dayfile(text, anchor_day, tz=tz)
        if not errors:
            return parsed, text

        # Reopen with errors as comments at the top.
        error_header = "\n".join(
            f"# !! ERROR line {e.line_no}: {e.message} -- {e.raw.rstrip()}"
            for e in errors
        )
        text = error_header + "\n#\n" + text
        if not confirm_fn("Parse errors found. Re-edit?", default=True):
            return None, text


def _stash_edit(text: str, paths) -> Path:
    """Save the user's last text to disk so a `n`-aborted edit isn't lost."""
    edits_dir = paths.base_dir / "edits"
    edits_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    p = edits_dir / f"{stamp}-aborted.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _echo_plan(plan: "Plan") -> None:
    """Print a diff plan with git-style coloring when connected to a TTY."""
    df = _lazy_dayfile()
    for line in df.format_plan(plan).splitlines():
        stripped = line.lstrip()
        if stripped.startswith("+ "):
            typer.secho(line, fg=typer.colors.GREEN)
        elif stripped.startswith("- "):
            typer.secho(line, fg=typer.colors.RED)
        elif stripped.startswith("~ "):
            typer.secho(line, fg=typer.colors.YELLOW)
        else:
            typer.echo(line)


def _build_update_patch(op) -> tuple[dict, Optional[int]]:
    """Build (patch_body, conference_data_version) for an UpdateOp."""
    ev = op.event
    line = op.parsed
    body: dict = {}
    if ev.title.strip() != line.title.strip():
        body["summary"] = line.title
    if _norm_for_compare(ev.start) != _norm_for_compare(line.start):
        body["start"] = _format_event_time(line.start)
    if _norm_for_compare(ev.end) != _norm_for_compare(line.end):
        body["end"] = _format_event_time(line.end)
    if (ev.location or "") != (line.location or ""):
        body["location"] = line.location or ""
    attendees_body = _attendees_patch(ev.attendees, line.invites)
    if attendees_body is not None:
        body["attendees"] = attendees_body
    conf_ver: Optional[int] = None
    if line.meet and not ev.meet:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
        conf_ver = 1
    return body, conf_ver


def _apply_plan(service, calendar_id: str, plan: "Plan") -> None:
    """Apply creates/updates/deletes via the Google Calendar API.

    When multiple operations are planned, sends them in a single HTTP batch
    request so all changes land on Google Calendar simultaneously.
    """
    gc = _lazy_google()
    update_patches = [
        (op.event.id, *_build_update_patch(op)) for op in plan.updates
    ]
    update_patches = [(eid, body, cv) for eid, body, cv in update_patches if body]
    total_ops = len(plan.deletes) + len(update_patches) + len(plan.creates)

    if total_ops <= 1:
        for op in plan.deletes:
            gc.delete_event(service, calendar_id, op.event.id)
        for eid, body, conf_ver in update_patches:
            if conf_ver is not None:
                gc.update_event(
                    service, calendar_id, eid, body, conference_data_version=conf_ver
                )
            else:
                gc.update_event(service, calendar_id, eid, body)
        for op in plan.creates:
            line = op.parsed
            body = _build_event_body(
                title=line.title,
                start=line.start,
                end=line.end,
                location=line.location,
                invite=line.invites,
                meet=line.meet,
            )
            service.events().insert(
                calendarId=calendar_id, body=body, conferenceDataVersion=1
            ).execute()
        return

    requests = []
    for op in plan.deletes:
        requests.append(
            service.events().delete(calendarId=calendar_id, eventId=op.event.id)
        )
    for eid, body, conf_ver in update_patches:
        kwargs: dict = {"calendarId": calendar_id, "eventId": eid, "body": body}
        if conf_ver is not None:
            kwargs["conferenceDataVersion"] = conf_ver
        requests.append(service.events().patch(**kwargs))
    for op in plan.creates:
        line = op.parsed
        body = _build_event_body(
            title=line.title,
            start=line.start,
            end=line.end,
            location=line.location,
            invite=line.invites,
            meet=line.meet,
        )
        requests.append(
            service.events().insert(
                calendarId=calendar_id, body=body, conferenceDataVersion=1
            )
        )
    gc.execute_batch(service, requests)


def _attendees_patch(
    existing: tuple[str, ...], invites: list[str]
) -> Optional[list[dict]]:
    """Return the `attendees` body to PATCH, or None if the set is unchanged.

    Google replaces the full attendee list on PATCH, so we always send all
    desired emails. Comparison is order- and case-insensitive; the body
    preserves the user-typed casing for new entries.
    """
    old_set = {e.strip().lower() for e in existing if e and e.strip()}
    new_set = {e.strip().lower() for e in invites if e and e.strip()}
    if old_set == new_set:
        return None
    seen: set[str] = set()
    deduped: list[str] = []
    for raw in invites:
        if not raw:
            continue
        cleaned = raw.strip()
        key = cleaned.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return [{"email": e} for e in deduped]


def _norm_for_compare(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(second=0, microsecond=0)
    from datetime import timezone

    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _calendar_display_name(service, calendar_id: str) -> str:
    """Return a human-friendly calendar label for dayfile headers."""
    if not calendar_id.endswith("@group.calendar.google.com"):
        return calendar_id
    gc = _lazy_google()
    try:
        for cal in gc.list_calendars(service):
            if cal.calendar_id == calendar_id and cal.summary:
                return cal.summary
    except Exception:  # noqa: BLE001
        pass
    return calendar_id


@app.command(name="edit", help="Open day(s) in $EDITOR for git-rebase-style editing.")
def edit_command(
    when: Annotated[
        Optional[list[str]],
        typer.Argument(
            metavar="WHEN...",
            help="Day spec: today, yesterday, fri, week, next week, 2026-05-10, …",
        ),
    ] = None,
    calendar: Annotated[
        Optional[str], typer.Option("--calendar", help="Calendar id or name")
    ] = None,
) -> None:
    when_str = " ".join(when) if when else "today"
    tu = _lazy_time_utils()
    df = _lazy_dayfile()
    now = datetime.now(_local_zone())
    try:
        start_day, end_day = tu.parse_day_spec(when_str, now)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    paths, service = _get_service()
    gc = _lazy_google()
    calendar_id = gc.resolve_calendar_id(
        service, paths, calendar, picker=_interactive_pick_calendar
    )

    tz = now.tzinfo
    time_min = datetime.combine(start_day, datetime_time.min, tzinfo=tz)
    time_max = datetime.combine(
        end_day + timedelta(days=1), datetime_time.min, tzinfo=tz
    )
    events = gc.list_events(service, calendar_id, time_min, time_max)

    calendar_label = _calendar_display_name(service, calendar_id)
    is_week = start_day != end_day
    if is_week:
        events_by_day: dict[date, list["Event"]] = {}
        d = start_day
        while d <= end_day:
            events_by_day[d] = []
            d += timedelta(days=1)
        for e in events:
            local_day = (e.start.astimezone(tz) if tz else e.start).date()
            if local_day in events_by_day:
                events_by_day[local_day].append(e)
        initial_text = df.render_week(events_by_day, calendar_label, tz=tz)
    else:
        initial_text = df.render_day(events, start_day, calendar_label, tz=tz)

    text = initial_text
    while True:
        parsed, text = _editor_session(text, start_day, tz)
        if parsed is None:
            typer.echo("Edit aborted; no changes applied.")
            return

        plan = df.diff_plan(events, parsed)
        if plan.is_empty():
            typer.echo("No changes.")
            return

        _echo_plan(plan)
        choice = typer.prompt("\nApply? [Y/n/e]", default="Y").strip().lower()
        if choice in ("", "y", "yes"):
            _apply_plan(service, calendar_id, plan)
            typer.echo(
                f"Applied: "
                f"{len(plan.creates)} create(s), "
                f"{len(plan.updates)} update(s), "
                f"{len(plan.deletes)} delete(s)."
            )
            return
        if choice in ("n", "no"):
            stash = _stash_edit(text, paths)
            typer.echo(f"Aborted; your edits stashed at {stash}.")
            return
        if choice == "e":
            continue
        typer.echo(f"Unrecognized choice {choice!r}. Aborting.")
        return


# Legacy `add` and `calendars` commands -----------------------------------


@app.command(
    name="add",
    hidden=True,
    help="[Deprecated] Use `cb <when> <title>` instead.",
)
def add_legacy(
    title: Annotated[str, typer.Argument(help="Event title")],
    last: Annotated[
        Optional[str], typer.Option("--last", help="Duration like 4h, 30m, 1h30m")
    ] = None,
    time_range: Annotated[
        Optional[str],
        typer.Option("--from", "--time", help='Time range like "9am-12pm"'),
    ] = None,
    location: Annotated[Optional[str], typer.Option(help="Event location")] = None,
    calendar: Annotated[
        Optional[str], typer.Option(help="Calendar id or summary")
    ] = None,
    invite: Annotated[
        Optional[list[str]],
        typer.Option("--invite", "-i", help="Email addresses to invite"),
    ] = None,
    meet: Annotated[
        bool,
        typer.Option("--meet", "--hangouts", "-m", help="Add Google Meet link"),
    ] = False,
    description: Annotated[
        Optional[str],
        typer.Option("--description", "--agenda", help="Event description."),
    ] = None,
    edit_description: Annotated[
        bool,
        typer.Option("--edit-description", "-e", help="Open editor for description."),
    ] = False,
) -> None:
    typer.echo(
        "Note: `cb add ... --last X` is deprecated. "
        "Try `cb <when> <title>` (e.g. `cb 4h Title`).",
        err=True,
    )

    if last is None and time_range is None:
        typer.echo(
            "Error: One of --last or --from/--time must be provided.", err=True
        )
        raise typer.Exit(code=1)
    if last is not None and time_range is not None:
        typer.echo(
            "Error: --last and --from/--time are mutually exclusive.", err=True
        )
        raise typer.Exit(code=1)

    tu = _lazy_time_utils()
    now = datetime.now(_local_zone())
    if last:
        tr = tu.parse_last(last, now)
    else:
        tr = tu.parse_from(time_range, now)

    final_description: Optional[str] = None
    if edit_description:
        final_description = _get_description_from_editor(description)
    elif description:
        final_description = description

    paths, service = _get_service()
    gc = _lazy_google()
    calendar_id = gc.resolve_calendar_id(
        service, paths, calendar, picker=_interactive_pick_calendar
    )

    body = _build_event_body(
        title=title,
        start=tr.start,
        end=tr.end,
        location=location,
        description=final_description,
        invite=invite,
        meet=meet,
    )

    created = (
        service.events()
        .insert(calendarId=calendar_id, body=body, conferenceDataVersion=1)
        .execute()
    )
    url = created.get("htmlLink")
    if url:
        typer.echo(f"Event created: {url}")
    else:
        typer.echo("Event created.")
    hangout = created.get("hangoutLink")
    if hangout:
        typer.echo(f"Meet URL: {hangout}")


@app.command(name="calendars", help="List available calendars (marks the default).")
def list_calendars_command() -> None:
    paths, service = _get_service()
    gc = _lazy_google()
    config = read_config(paths)
    default_id = config.get("default_calendar_id")
    for calendar in gc.list_calendars(service):
        marker = "  [default]" if calendar.calendar_id == default_id else ""
        typer.echo(
            f"{calendar.summary} ({calendar.access_role}) - {calendar.calendar_id}{marker}"
        )


# `cb cals` as a short alias for `cb calendars`.
@app.command(name="cals", help="Alias for `calendars`.", hidden=True)
def list_calendars_alias() -> None:
    list_calendars_command()


@app.command(
    name="set-default",
    help="Set the default calendar (by id or name). Subsequent commands use it.",
)
def set_default_calendar(
    calendar: Annotated[
        str,
        typer.Argument(
            help='Calendar id or name (e.g. "you@example.com" or "Personal")'
        ),
    ],
) -> None:
    paths, service = _get_service()
    gc = _lazy_google()
    calendars = gc.list_calendars(service)
    match = next(
        (
            c
            for c in calendars
            if c.calendar_id == calendar or c.summary == calendar
        ),
        None,
    )
    if match is None:
        typer.echo(
            f'Calendar "{calendar}" not found. Run `cb cals` to see options.',
            err=True,
        )
        raise typer.Exit(code=1)
    if match.access_role not in {"writer", "owner"}:
        typer.echo(
            f'Calendar "{match.summary}" is read-only ({match.access_role}); '
            "cannot use as default for adding events.",
            err=True,
        )
        raise typer.Exit(code=1)

    config = read_config(paths)
    config["default_calendar_id"] = match.calendar_id
    write_config(paths, config)
    typer.echo(f'Default calendar set to "{match.summary}" ({match.calendar_id}).')


_KNOWN_SUBCOMMANDS = {
    "capture",
    "edit",
    "add",
    "calendars",
    "cals",
    "set-default",
}


def _normalize_argv(argv: list[str]) -> list[str]:
    """If the first non-flag token is not a known subcommand, inject `capture`.

    This lets `cb 4h Title` work without an explicit subcommand, while still
    dispatching `cb edit today`, `cb cals`, `cb set-default`, etc. correctly.
    """
    first_non_flag = next((a for a in argv if not a.startswith("-")), None)
    if first_non_flag is None:
        return argv
    if first_non_flag in _KNOWN_SUBCOMMANDS:
        return argv
    return ["capture", *argv]


def main() -> None:
    argv = _normalize_argv(sys.argv[1:])
    app(args=argv)


if __name__ == "__main__":
    main()

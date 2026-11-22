from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from calbase.config import AppPaths, read_config, write_config
from calbase.google_calendar import (
    delete_event,
    list_events,
    resolve_calendar_id,
    update_event,
)


class _FakeCalendarList:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def list(self) -> "_FakeCalendarList":
        return self

    def execute(self) -> dict:
        return {"items": self._items}


class _FakeService:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def calendarList(self) -> _FakeCalendarList:
        return _FakeCalendarList(self._items)


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths(
        base_dir=tmp_path,
        config_path=tmp_path / "config.json",
        token_path=tmp_path / "token.json",
        credentials_path=tmp_path / "credentials.json",
    )


def test_resolve_calendar_id_override_read_only(tmp_path: Path) -> None:
    service = _FakeService(
        [
            {"id": "ro", "summary": "ReadOnly", "accessRole": "reader"},
        ]
    )
    with pytest.raises(ValueError):
        resolve_calendar_id(service, _paths(tmp_path), "ReadOnly")


def test_resolve_calendar_id_override_writer(tmp_path: Path) -> None:
    service = _FakeService(
        [
            {"id": "ro", "summary": "ReadOnly", "accessRole": "reader"},
            {"id": "wr", "summary": "Writable", "accessRole": "writer"},
        ]
    )
    assert resolve_calendar_id(service, _paths(tmp_path), "wr") == "wr"


def test_resolve_calendar_id_uses_saved_default(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    write_config(paths, {"default_calendar_id": "saved"})
    service = _FakeService(
        [
            {"id": "saved", "summary": "Saved", "accessRole": "owner"},
        ]
    )
    assert resolve_calendar_id(service, paths, None) == "saved"


def test_resolve_calendar_id_stale_default_repicks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    write_config(paths, {"default_calendar_id": "stale@example.com"})
    service = _FakeService(
        [
            {"id": "owned", "summary": "Owned", "accessRole": "owner"},
        ]
    )
    assert resolve_calendar_id(service, paths, None) == "owned"
    assert read_config(paths)["default_calendar_id"] == "owned"
    captured = capsys.readouterr()
    assert "no longer accessible" in captured.err


def test_resolve_calendar_id_default_demoted_to_reader_repicks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    write_config(paths, {"default_calendar_id": "demoted"})
    service = _FakeService(
        [
            {"id": "demoted", "summary": "Demoted", "accessRole": "reader"},
            {"id": "owned", "summary": "Owned", "accessRole": "owner"},
        ]
    )
    assert resolve_calendar_id(service, paths, None) == "owned"
    captured = capsys.readouterr()
    assert "no longer accessible" in captured.err


def test_resolve_calendar_id_picks_first_owned(tmp_path: Path) -> None:
    service = _FakeService(
        [
            {"id": "wr", "summary": "Writable", "accessRole": "writer"},
            {"id": "own", "summary": "Owned", "accessRole": "owner"},
        ]
    )
    paths = _paths(tmp_path)
    assert resolve_calendar_id(service, paths, None) == "own"
    assert read_config(paths)["default_calendar_id"] == "own"


def test_resolve_calendar_id_falls_back_to_writer_when_no_owner(
    tmp_path: Path,
) -> None:
    service = _FakeService(
        [
            {"id": "wr", "summary": "Writable", "accessRole": "writer"},
        ]
    )
    assert resolve_calendar_id(service, _paths(tmp_path), None) == "wr"


def test_resolve_calendar_id_no_writable_raises(tmp_path: Path) -> None:
    service = _FakeService(
        [
            {"id": "ro", "summary": "Read", "accessRole": "reader"},
        ]
    )
    with pytest.raises(RuntimeError):
        resolve_calendar_id(service, _paths(tmp_path), None)


def test_resolve_calendar_id_picker_called_on_no_default(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    service = _FakeService(
        [
            {"id": "a", "summary": "A", "accessRole": "owner"},
            {"id": "b", "summary": "B", "accessRole": "writer"},
            {"id": "c", "summary": "C", "accessRole": "reader"},
        ]
    )
    seen_args: dict = {}

    def picker(writable, stale_id):
        seen_args["writable"] = writable
        seen_args["stale_id"] = stale_id
        return next(c for c in writable if c.calendar_id == "b")

    assert resolve_calendar_id(service, paths, None, picker=picker) == "b"
    assert {c.calendar_id for c in seen_args["writable"]} == {"a", "b"}
    assert seen_args["stale_id"] is None
    assert read_config(paths)["default_calendar_id"] == "b"


def test_resolve_calendar_id_picker_called_when_default_stale(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    write_config(paths, {"default_calendar_id": "gone@example.com"})
    service = _FakeService(
        [
            {"id": "a", "summary": "A", "accessRole": "owner"},
        ]
    )
    received: dict = {}

    def picker(writable, stale_id):
        received["stale_id"] = stale_id
        return writable[0]

    assert resolve_calendar_id(service, paths, None, picker=picker) == "a"
    assert received["stale_id"] == "gone@example.com"


# Events helpers ----------------------------------------------------------


class _FakeEventsCall:
    def __init__(self, owner: "_FakeEvents", op: str, **kwargs) -> None:
        self.owner = owner
        self.op = op
        self.kwargs = kwargs

    def execute(self):
        self.owner.calls.append((self.op, self.kwargs))
        return self.owner.responses.get(self.op, {})


class _FakeEvents:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.responses: dict[str, object] = {}

    def list(self, **kwargs):
        return _FakeEventsCall(self, "list", **kwargs)

    def patch(self, **kwargs):
        return _FakeEventsCall(self, "patch", **kwargs)

    def delete(self, **kwargs):
        return _FakeEventsCall(self, "delete", **kwargs)


class _FakeEventsService:
    def __init__(self, list_response: dict | None = None) -> None:
        self._events = _FakeEvents()
        if list_response is not None:
            self._events.responses["list"] = list_response

    def events(self) -> _FakeEvents:
        return self._events


def test_list_events_maps_api_payload() -> None:
    response = {
        "items": [
            {
                "id": "abc",
                "summary": "Standup",
                "start": {"dateTime": "2026-05-07T09:00:00+00:00"},
                "end": {"dateTime": "2026-05-07T09:30:00+00:00"},
                "location": "office",
                "attendees": [{"email": "alice@x.com"}, {"email": "bob@x.com"}],
                "hangoutLink": "https://meet.google.com/xyz",
            },
            {
                "id": "all-day",
                "summary": "Holiday",
                "start": {"date": "2026-05-07"},
                "end": {"date": "2026-05-08"},
            },
        ]
    }
    service = _FakeEventsService(list_response=response)
    events = list_events(
        service,
        "cal",
        datetime(2026, 5, 7, tzinfo=timezone.utc),
        datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    assert len(events) == 1
    e = events[0]
    assert e.id == "abc"
    assert e.title == "Standup"
    assert e.start.hour == 9 and e.start.minute == 0
    assert e.location == "office"
    assert set(e.attendees) == {"alice@x.com", "bob@x.com"}
    assert e.meet is True

    op, kwargs = service.events().calls[0]
    assert op == "list"
    assert kwargs["calendarId"] == "cal"
    assert kwargs["singleEvents"] is True
    assert kwargs["orderBy"] == "startTime"


def test_update_event_passes_body() -> None:
    service = _FakeEventsService()
    update_event(service, "cal", "abc", {"summary": "New title"})
    op, kwargs = service.events().calls[0]
    assert op == "patch"
    assert kwargs == {
        "calendarId": "cal",
        "eventId": "abc",
        "body": {"summary": "New title"},
    }


def test_delete_event_calls_api() -> None:
    service = _FakeEventsService()
    delete_event(service, "cal", "abc")
    op, kwargs = service.events().calls[0]
    assert op == "delete"
    assert kwargs == {"calendarId": "cal", "eventId": "abc"}

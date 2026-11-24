from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from calbase import __version__
from calbase.cli import _format_event_time, _normalize_argv, app
from calbase.dayfile import Event

runner = CliRunner()


def invoke(args: list[str]):
    """Mirror what `main()` does so tests exercise the same dispatch."""
    return runner.invoke(app, _normalize_argv(args))


def test_version_flag():
    result = invoke(["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


@pytest.fixture
def mock_dependencies():
    with patch("calbase.google_calendar.authenticate"), \
         patch("calbase.google_calendar.build_calendar_service") as mock_build, \
         patch("calbase.google_calendar.resolve_calendar_id") as mock_resolve:

        mock_service = MagicMock()
        mock_events = MagicMock()
        mock_insert = MagicMock()

        mock_build.return_value = mock_service
        mock_service.events.return_value = mock_events
        mock_events.insert.return_value = mock_insert
        mock_insert.execute.return_value = {
            "htmlLink": "http://event",
            "hangoutLink": "http://meet",
        }

        mock_resolve.return_value = "primary"

        yield {
            "service": mock_service,
            "execute": mock_insert.execute,
            "resolve": mock_resolve,
        }


def test_add_event_basic(mock_dependencies):
    result = runner.invoke(app, ["add", "Test Event", "--last", "1h"])
    assert result.exit_code == 0
    assert "Event created: http://event" in result.stdout

    mock_dependencies["execute"].assert_called_once()
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["summary"] == "Test Event"
    assert "description" not in body


def test_add_event_with_description_value(mock_dependencies):
    result = runner.invoke(
        app, ["add", "Test Event", "--last", "1h", "--description", "My Desc"]
    )
    assert result.exit_code == 0

    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["summary"] == "Test Event"
    assert body["description"] == "My Desc"


def test_add_event_with_editor(mock_dependencies):
    with patch("calbase.cli._edit_text") as mock_edit:
        mock_edit.return_value = "Line 1\n# Comment\nLine 2"

        result = runner.invoke(
            app, ["add", "Test Event", "--last", "1h", "--edit-description"]
        )
        assert result.exit_code == 0
        mock_edit.assert_called_once()

        body = mock_dependencies["service"].events().insert.call_args[1]["body"]
        assert body["summary"] == "Test Event"
        assert body["description"] == "Line 1\nLine 2"


def test_add_event_with_editor_empty(mock_dependencies):
    with patch("calbase.cli._edit_text") as mock_edit:
        mock_edit.return_value = None

        result = runner.invoke(
            app, ["add", "Test Event", "--last", "1h", "--edit-description"]
        )
        assert result.exit_code == 0

        body = mock_dependencies["service"].events().insert.call_args[1]["body"]
        assert body["summary"] == "Test Event"
        assert "description" not in body


def test_list_calendars(mock_dependencies):
    mock_calendar_list = MagicMock()
    mock_calendar_list.summary = "My Calendar"
    mock_calendar_list.access_role = "owner"
    mock_calendar_list.calendar_id = "cal-id-123"

    with patch("calbase.google_calendar.list_calendars") as mock_list:
        mock_list.return_value = [mock_calendar_list]
        result = runner.invoke(app, ["calendars"])
        assert result.exit_code == 0
        assert "My Calendar (owner) - cal-id-123" in result.stdout


# Capture form: `cb <when> <title>` -------------------------------------


def test_capture_duration_form(mock_dependencies):
    result = invoke(["1h", "Office", "cleanup"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["summary"] == "Office cleanup"
    assert "start" in body
    assert "end" in body


def test_capture_today_range_form(mock_dependencies):
    result = invoke(["9a-12p", "Deep", "work"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["summary"] == "Deep work"


def test_capture_day_prefixed_range(mock_dependencies):
    result = invoke(["tomorrow", "9-10a", "1:1", "with", "Alex"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["summary"] == "1:1 with Alex"


def test_capture_postfix_location_extracted(mock_dependencies):
    result = invoke(["1h", "Sync", "@office"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["summary"] == "Sync"
    assert body["location"] == "office"


def test_capture_postfix_meet_extracted(mock_dependencies):
    result = invoke(["1h", "Sync", "+meet"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["summary"] == "Sync"
    assert "conferenceData" in body


def test_capture_short_meet_flag(mock_dependencies):
    result = invoke(["1h", "Sync", "-m"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert "conferenceData" in body


def test_capture_invite_flag_repeats(mock_dependencies):
    result = invoke(["1h", "Sync", "-i", "a@x.com", "-i", "b@x.com"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    emails = [a["email"] for a in body["attendees"]]
    assert emails == ["a@x.com", "b@x.com"]


def test_capture_postfix_invite_extracted(mock_dependencies):
    result = invoke(["1h", "Sync", "+invite:alice@x.com"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["attendees"] == [{"email": "alice@x.com"}]


def test_capture_postfix_repeat_weekdays(mock_dependencies):
    result = invoke(["9a-9:30a", "Standup", "+meet", "+repeat:weekdays"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["summary"] == "Standup"
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]


def test_capture_postfix_repeat_business_days(mock_dependencies):
    result = invoke(["9a-9:30a", "Standup", "+repeat:business"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]


def test_capture_repeat_flag(mock_dependencies):
    result = invoke(["9a-10a", "Sync", "-r", "mon,wed,fri"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"]


def test_capture_postfix_rrule(mock_dependencies):
    result = invoke(["9a-10a", "Board", "+rrule:FREQ=MONTHLY;BYDAY=1MO;COUNT=6"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["summary"] == "Board"
    assert body["recurrence"] == ["RRULE:FREQ=MONTHLY;BYDAY=1MO;COUNT=6"]


def test_capture_rrule_flag(mock_dependencies):
    result = invoke(["9a-10a", "Board", "--rrule", "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"])
    assert result.exit_code == 0
    body = mock_dependencies["service"].events().insert.call_args[1]["body"]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"]


def test_capture_repeat_and_rrule_conflict_errors(mock_dependencies):
    result = invoke(["9a-10a", "Sync", "+repeat:daily", "+rrule:FREQ=DAILY"])
    assert result.exit_code == 1


def test_capture_invalid_repeat_errors(mock_dependencies):
    result = invoke(["9a-10a", "Sync", "+repeat:bogus"])
    assert result.exit_code == 1


def test_capture_bad_when_token_errors_with_examples(mock_dependencies):
    result = invoke(["banana", "Title"])
    assert result.exit_code == 1


def test_legacy_add_alias_still_works(mock_dependencies):
    result = runner.invoke(app, ["add", "Test Event", "--last", "1h"])
    assert result.exit_code == 0


# Edit flow: `cb edit <when>` -------------------------------------------


@pytest.fixture
def edit_dependencies():
    """Patch authenticate + service + resolve + list_events for edit tests."""
    with patch("calbase.google_calendar.authenticate"), \
         patch("calbase.google_calendar.build_calendar_service") as mock_build, \
         patch("calbase.google_calendar.resolve_calendar_id") as mock_resolve, \
         patch("calbase.google_calendar.list_events") as mock_list_events, \
         patch("calbase.google_calendar.update_event") as mock_update, \
         patch("calbase.google_calendar.delete_event") as mock_delete:
        service = MagicMock()
        mock_build.return_value = service
        mock_resolve.return_value = "cal"
        today = datetime.now().astimezone().date()
        evt = Event(
            id="abc",
            title="Standup",
            start=datetime(today.year, today.month, today.day, 9, 0).astimezone(),
            end=datetime(today.year, today.month, today.day, 9, 30).astimezone(),
        )
        mock_list_events.return_value = [evt]
        service.events().insert().execute.return_value = {
            "htmlLink": "http://event"
        }
        yield {
            "service": service,
            "list_events": mock_list_events,
            "update_event": mock_update,
            "delete_event": mock_delete,
            "today": today,
        }


def test_edit_today_no_changes(edit_dependencies):
    """If user saves the dayfile unchanged, no API mutations occur."""
    with patch("calbase.cli._edit_text") as mock_edit:
        mock_edit.side_effect = lambda text: text
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
        assert "No changes." in result.stdout
    assert edit_dependencies["update_event"].call_count == 0
    assert edit_dependencies["delete_event"].call_count == 0


def test_edit_today_mutation_creates_new_event(edit_dependencies):
    """User adds a line; we apply on `Y` and call insert()."""
    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.prompt") as mock_prompt:
        mock_edit.side_effect = lambda text: text + "10:00-11:00  New event\n"
        mock_prompt.return_value = "Y"
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
        assert "Applied" in result.stdout
    insert_calls = edit_dependencies["service"].events().insert.call_args_list
    assert any(
        call.kwargs.get("body", {}).get("summary") == "New event"
        for call in insert_calls
    )


def test_edit_today_creates_recurring_event(edit_dependencies):
    """Adding a line with `+repeat:weekdays` sends `recurrence` to insert()."""
    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.prompt") as mock_prompt:
        mock_edit.side_effect = (
            lambda text: text + "10:00-10:30  Team check-in +repeat:weekdays\n"
        )
        mock_prompt.return_value = "Y"
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
    insert_calls = edit_dependencies["service"].events().insert.call_args_list
    assert any(
        call.kwargs.get("body", {}).get("recurrence")
        == ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]
        for call in insert_calls
    )


def test_edit_today_creates_event_with_rrule(edit_dependencies):
    """Adding a line with `+rrule:` sends the normalized RRULE to insert()."""
    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.prompt") as mock_prompt:
        mock_edit.side_effect = (
            lambda text: text + "11:00-12:00  Monthly retro +rrule:FREQ=MONTHLY;BYDAY=1MO\n"
        )
        mock_prompt.return_value = "Y"
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
    insert_calls = edit_dependencies["service"].events().insert.call_args_list
    assert any(
        call.kwargs.get("body", {}).get("recurrence")
        == ["RRULE:FREQ=MONTHLY;BYDAY=1MO"]
        for call in insert_calls
    )


def test_edit_today_repeat_added_to_existing_patches_recurrence(edit_dependencies):
    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.prompt") as mock_prompt:
        def add_repeat(text: str) -> str:
            out_lines = []
            for line in text.splitlines():
                if line.lstrip().startswith("09:00-09:30") and "# id:abc" in line:
                    head, _, tail = line.partition("# id:abc")
                    line = f"{head.rstrip()} +repeat:mon-fri  # id:abc{tail}"
                out_lines.append(line)
            return "\n".join(out_lines) + "\n"

        mock_edit.side_effect = add_repeat
        mock_prompt.return_value = "y"
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
    edit_dependencies["update_event"].assert_called_once()
    _, _, _, body = edit_dependencies["update_event"].call_args[0]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]


def test_edit_today_invite_added_syncs_to_google(edit_dependencies):
    """Adding `+invite:` to an existing line must PATCH attendees to Google."""
    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.prompt") as mock_prompt:
        def add_invite(text: str) -> str:
            out_lines = []
            for line in text.splitlines():
                if line.lstrip().startswith("09:00-09:30") and "# id:abc" in line:
                    head, _, tail = line.partition("# id:abc")
                    line = f"{head.rstrip()} +invite:alice@example.com  # id:abc{tail}"
                out_lines.append(line)
            return "\n".join(out_lines) + "\n"

        mock_edit.side_effect = add_invite
        mock_prompt.return_value = "y"
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
    edit_dependencies["update_event"].assert_called_once()
    _, _, _, body = edit_dependencies["update_event"].call_args[0]
    assert body["attendees"] == [{"email": "alice@example.com"}]


def test_edit_today_invite_removed_syncs_to_google(edit_dependencies):
    """Stripping an attendee from the line must PATCH attendees=[] to Google."""
    today = edit_dependencies["today"]
    evt = Event(
        id="abc",
        title="Standup",
        start=datetime(today.year, today.month, today.day, 9, 0).astimezone(),
        end=datetime(today.year, today.month, today.day, 9, 30).astimezone(),
        attendees=("alice@example.com",),
    )
    edit_dependencies["list_events"].return_value = [evt]

    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.prompt") as mock_prompt:
        def strip_invite(text: str) -> str:
            return text.replace(" +invite:alice@example.com", "")

        mock_edit.side_effect = strip_invite
        mock_prompt.return_value = "y"
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
    edit_dependencies["update_event"].assert_called_once()
    _, _, _, body = edit_dependencies["update_event"].call_args[0]
    assert body["attendees"] == []


def test_edit_today_invite_unchanged_skips_update(edit_dependencies):
    """No-op attendee edits (reorder, case) must NOT trigger a PATCH."""
    today = edit_dependencies["today"]
    evt = Event(
        id="abc",
        title="Standup",
        start=datetime(today.year, today.month, today.day, 9, 0).astimezone(),
        end=datetime(today.year, today.month, today.day, 9, 30).astimezone(),
        attendees=("a@x.com", "B@x.com"),
    )
    edit_dependencies["list_events"].return_value = [evt]

    with patch("calbase.cli._edit_text") as mock_edit:
        def reorder(text: str) -> str:
            return text.replace(
                "+invite:a@x.com +invite:B@x.com",
                "+invite:b@x.com +invite:A@x.com",
            )

        mock_edit.side_effect = reorder
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
        assert "No changes." in result.stdout
    assert edit_dependencies["update_event"].call_count == 0


def test_edit_today_delete_via_missing_line(edit_dependencies):
    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.prompt") as mock_prompt:
        mock_edit.side_effect = lambda text: "\n".join(
            line
            for line in text.splitlines()
            if not line.strip() or line.lstrip().startswith("#")
        ) + "\n"
        mock_prompt.return_value = "y"
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
    edit_dependencies["delete_event"].assert_called_once_with(
        edit_dependencies["service"], "cal", "abc"
    )


def test_edit_user_aborts_with_n(edit_dependencies, tmp_path):
    from calbase import config as config_mod

    fake_paths = config_mod.AppPaths(
        base_dir=tmp_path,
        config_path=tmp_path / "config.json",
        token_path=tmp_path / "token.json",
        credentials_path=tmp_path / "credentials.json",
    )
    with patch("calbase.cli.get_app_paths", return_value=fake_paths), \
         patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.prompt") as mock_prompt:
        mock_edit.side_effect = lambda text: text + "10:00-11:00  Throwaway\n"
        mock_prompt.return_value = "n"
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
        assert "stashed" in result.stdout.lower() or "abort" in result.stdout.lower()
    insert_calls = edit_dependencies["service"].events().insert.call_args_list
    new_calls = [c for c in insert_calls if c.kwargs.get("body")]
    assert new_calls == []


def test_edit_parse_error_retries_then_aborts(edit_dependencies):
    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.confirm") as mock_confirm:
        mock_edit.side_effect = ["this is not a valid line\n"]
        mock_confirm.return_value = False
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
        assert mock_confirm.call_count == 1


def test_edit_parse_error_then_fix_then_apply(edit_dependencies):
    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.confirm") as mock_confirm:
        mock_edit.side_effect = [
            "junk line\n",
            None,
        ]
        mock_confirm.return_value = False
        result = invoke(["edit", "today"])
        assert result.exit_code == 0


def test_edit_invalid_when_errors(edit_dependencies):
    result = invoke(["edit", "banana"])
    assert result.exit_code == 1


def test_edit_next_week_unquoted(edit_dependencies):
    edit_dependencies["list_events"].return_value = []
    with patch("calbase.cli._edit_text") as mock_edit:
        mock_edit.side_effect = lambda text: text
        result = invoke(["edit", "next", "week"])
        assert result.exit_code == 0
        assert "No changes." in result.stdout


def test_edit_multiple_ops_uses_batch_request(edit_dependencies):
    with patch("calbase.cli._edit_text") as mock_edit, \
         patch("calbase.cli.typer.prompt", return_value="y"), \
         patch("calbase.google_calendar.execute_batch") as mock_batch:
        # Update existing Standup + create 2 new events -> 3 ops batched together.
        mock_edit.side_effect = lambda text: (
            text.replace("Standup", "Daily standup")
            + "10:00-11:00  Deep work\n"
            + "17:30-18:30  Gym\n"
        )
        result = invoke(["edit", "today"])
        assert result.exit_code == 0
        assert "Applied: 2 create(s), 1 update(s), 0 delete(s)." in result.stdout
    mock_batch.assert_called_once()
    _, reqs = mock_batch.call_args[0]
    assert len(reqs) == 3


# _format_event_time -----------------------------------------------------


def test_format_event_time_with_zoneinfo_sends_iana_name() -> None:
    dt = datetime(2026, 5, 7, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    payload = _format_event_time(dt)
    assert payload["dateTime"].startswith("2026-05-07T09:00:00")
    assert payload["timeZone"] == "America/Los_Angeles"


def test_format_event_time_with_fixed_offset_omits_timezone() -> None:
    """A fixed-offset tzinfo has no IANA name, so we omit `timeZone`."""
    dt = datetime(2026, 5, 7, 9, 0, tzinfo=timezone(timedelta(hours=-7)))
    payload = _format_event_time(dt)
    assert "dateTime" in payload
    assert "timeZone" not in payload
    assert payload["dateTime"].endswith("-07:00")


def test_format_event_time_naive_uses_utc() -> None:
    dt = datetime(2026, 5, 7, 9, 0)
    payload = _format_event_time(dt)
    assert payload["timeZone"] == "UTC"


# cb set-default ---------------------------------------------------------


def _make_calendar_info(id: str, summary: str, access_role: str):
    info = MagicMock()
    info.calendar_id = id
    info.summary = summary
    info.access_role = access_role
    return info


def test_set_default_writes_config(tmp_path):
    from calbase import config as config_mod

    fake_paths = config_mod.AppPaths(
        base_dir=tmp_path,
        config_path=tmp_path / "config.json",
        token_path=tmp_path / "token.json",
        credentials_path=tmp_path / "credentials.json",
    )
    with patch("calbase.google_calendar.authenticate"), \
         patch("calbase.google_calendar.build_calendar_service") as mock_build, \
         patch("calbase.cli.get_app_paths", return_value=fake_paths), \
         patch("calbase.google_calendar.list_calendars") as mock_list:
        mock_build.return_value = MagicMock()
        mock_list.return_value = [
            _make_calendar_info("alex@example.com", "alex@example.com", "writer"),
            _make_calendar_info("personal", "Personal", "owner"),
        ]
        result = runner.invoke(app, ["set-default", "alex@example.com"])
        assert result.exit_code == 0
        assert "Default calendar set" in result.stdout
    saved = config_mod.read_config(fake_paths)
    assert saved["default_calendar_id"] == "alex@example.com"


def test_set_default_unknown_calendar_errors(tmp_path):
    from calbase import config as config_mod

    fake_paths = config_mod.AppPaths(
        base_dir=tmp_path,
        config_path=tmp_path / "config.json",
        token_path=tmp_path / "token.json",
        credentials_path=tmp_path / "credentials.json",
    )
    with patch("calbase.google_calendar.authenticate"), \
         patch("calbase.google_calendar.build_calendar_service") as mock_build, \
         patch("calbase.cli.get_app_paths", return_value=fake_paths), \
         patch("calbase.google_calendar.list_calendars") as mock_list:
        mock_build.return_value = MagicMock()
        mock_list.return_value = [
            _make_calendar_info("personal", "Personal", "owner"),
        ]
        result = runner.invoke(app, ["set-default", "does-not-exist@example.com"])
        assert result.exit_code == 1


def test_set_default_read_only_errors(tmp_path):
    from calbase import config as config_mod

    fake_paths = config_mod.AppPaths(
        base_dir=tmp_path,
        config_path=tmp_path / "config.json",
        token_path=tmp_path / "token.json",
        credentials_path=tmp_path / "credentials.json",
    )
    with patch("calbase.google_calendar.authenticate"), \
         patch("calbase.google_calendar.build_calendar_service") as mock_build, \
         patch("calbase.cli.get_app_paths", return_value=fake_paths), \
         patch("calbase.google_calendar.list_calendars") as mock_list:
        mock_build.return_value = MagicMock()
        mock_list.return_value = [
            _make_calendar_info("holidays", "Holidays", "reader"),
        ]
        result = runner.invoke(app, ["set-default", "Holidays"])
        assert result.exit_code == 1


def test_calendars_marks_default(tmp_path):
    from calbase import config as config_mod

    fake_paths = config_mod.AppPaths(
        base_dir=tmp_path,
        config_path=tmp_path / "config.json",
        token_path=tmp_path / "token.json",
        credentials_path=tmp_path / "credentials.json",
    )
    config_mod.write_config(fake_paths, {"default_calendar_id": "personal"})
    with patch("calbase.google_calendar.authenticate"), \
         patch("calbase.google_calendar.build_calendar_service") as mock_build, \
         patch("calbase.cli.get_app_paths", return_value=fake_paths), \
         patch("calbase.google_calendar.list_calendars") as mock_list:
        mock_build.return_value = MagicMock()
        mock_list.return_value = [
            _make_calendar_info("personal", "Personal", "owner"),
            _make_calendar_info("work", "Work", "writer"),
        ]
        result = runner.invoke(app, ["calendars"])
        assert result.exit_code == 0
        assert "Personal (owner) - personal  [default]" in result.stdout
        assert "Work (writer) - work" in result.stdout
        assert "Work (writer) - work  [default]" not in result.stdout

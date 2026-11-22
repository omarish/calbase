from __future__ import annotations

from datetime import date, datetime, timezone

from calbase.dayfile import (
    Event,
    diff_plan,
    format_plan,
    parse_dayfile,
    render_day,
    render_week,
)


UTC = timezone.utc
ANCHOR = date(2026, 5, 7)


def _evt(
    id: str,
    title: str,
    h_start: int,
    h_end: int,
    *,
    minute_start: int = 0,
    minute_end: int = 0,
    location: str | None = None,
    attendees: tuple[str, ...] = (),
    meet: bool = False,
    day: date = ANCHOR,
) -> Event:
    return Event(
        id=id,
        title=title,
        start=datetime(day.year, day.month, day.day, h_start, minute_start, tzinfo=UTC),
        end=datetime(day.year, day.month, day.day, h_end, minute_end, tzinfo=UTC),
        location=location,
        attendees=attendees,
        meet=meet,
    )


def test_render_day_empty_includes_example_hint() -> None:
    text = render_day([], ANCHOR, "cal", tz=UTC)
    assert "Your day is empty" in text
    assert "09:00-09:30  Standup" in text
    assert "@office" in text
    parsed, errors = parse_dayfile(text, ANCHOR, tz=UTC)
    assert parsed == []
    assert errors == []


def test_render_day_with_events_omits_hint() -> None:
    text = render_day(
        [_evt("abc", "Standup", 9, 9, minute_end=30)], ANCHOR, "cal", tz=UTC
    )
    assert "Your day is empty" not in text


def test_render_week_all_days_empty_includes_hint() -> None:
    mon = date(2026, 5, 11)
    tue = date(2026, 5, 12)
    text = render_week({mon: [], tue: []}, "cal", tz=UTC)
    assert "Your day is empty" in text
    parsed, errors = parse_dayfile(text, mon, tz=UTC)
    assert parsed == []
    assert errors == []


def test_render_week_with_some_events_omits_hint() -> None:
    mon = date(2026, 5, 11)
    tue = date(2026, 5, 12)
    text = render_week(
        {mon: [_evt("a", "Sync", 9, 10, day=mon)], tue: []}, "cal", tz=UTC
    )
    assert "Your day is empty" not in text


def test_render_day_contains_header_and_lines() -> None:
    text = render_day(
        [_evt("abc", "Standup", 9, 9, minute_end=30)],
        ANCHOR,
        "test@example.com",
        tz=UTC,
    )
    assert "calbase edit" in text
    assert "Thursday" in text
    assert "test@example.com" in text
    assert "09:00-09:30" in text
    assert "Standup" in text
    assert "# id:abc" in text


def test_render_day_round_trip_basic() -> None:
    events = [
        _evt("abc", "Standup", 9, 9, minute_end=30),
        _evt("def", "Deep work", 9, 11, minute_start=30),
    ]
    text = render_day(events, ANCHOR, "cal", tz=UTC)
    parsed, errors = parse_dayfile(text, ANCHOR, tz=UTC)
    assert errors == []
    assert len(parsed) == 2
    assert parsed[0].title == "Standup"
    assert parsed[0].source_id == "abc"
    assert parsed[0].start.hour == 9 and parsed[0].start.minute == 0
    assert parsed[0].end.hour == 9 and parsed[0].end.minute == 30
    assert parsed[1].title == "Deep work"
    assert parsed[1].source_id == "def"


def test_render_day_round_trip_with_modifiers() -> None:
    events = [
        _evt("ghi", "Sync with Alex", 11, 12, location="office", meet=True),
    ]
    text = render_day(events, ANCHOR, "cal", tz=UTC)
    assert "@office" in text
    assert "+meet" in text
    parsed, errors = parse_dayfile(text, ANCHOR, tz=UTC)
    assert errors == []
    assert parsed[0].location == "office"
    assert parsed[0].meet is True
    assert parsed[0].title == "Sync with Alex"


def test_round_trip_quoted_location_with_spaces() -> None:
    events = [_evt("zzz", "Meeting", 14, 15, location="office park")]
    text = render_day(events, ANCHOR, "cal", tz=UTC)
    assert '@"office park"' in text
    parsed, errors = parse_dayfile(text, ANCHOR, tz=UTC)
    assert errors == []
    assert parsed[0].location == "office park"


def test_round_trip_attendees() -> None:
    events = [
        _evt("xyz", "Sync", 10, 11, attendees=("a@x.com", "b@x.com"), meet=True),
    ]
    text = render_day(events, ANCHOR, "cal", tz=UTC)
    parsed, errors = parse_dayfile(text, ANCHOR, tz=UTC)
    assert errors == []
    assert parsed[0].invites == ["a@x.com", "b@x.com"]
    assert parsed[0].meet is True


def test_parse_error_bad_time_prefix() -> None:
    text = "this line is junk\n09:00-09:30 Valid # id:abc\n"
    parsed, errors = parse_dayfile(text, ANCHOR, tz=UTC)
    assert len(parsed) == 1
    assert parsed[0].source_id == "abc"
    assert len(errors) == 1
    assert errors[0].line_no == 1


def test_parse_error_missing_title() -> None:
    text = "09:00-09:30  \n"
    parsed, errors = parse_dayfile(text, ANCHOR, tz=UTC)
    assert parsed == []
    assert len(errors) == 1
    assert errors[0].line_no == 1


def test_parse_overnight_wraps_end_to_next_day() -> None:
    text = "23:00-01:00  Late night debug\n"
    parsed, errors = parse_dayfile(text, ANCHOR, tz=UTC)
    assert errors == []
    assert parsed[0].start.day == ANCHOR.day
    assert parsed[0].end.day == ANCHOR.day + 1


def test_parse_skips_comment_and_blank_lines() -> None:
    text = (
        "# calbase edit — Thursday May 7\n"
        "# Edit, add, delete, …\n"
        "\n"
        "09:00-09:30  Standup  # id:abc\n"
    )
    parsed, errors = parse_dayfile(text, ANCHOR, tz=UTC)
    assert errors == []
    assert len(parsed) == 1


# diff_plan ---------------------------------------------------------------


def test_diff_no_changes() -> None:
    events = [_evt("abc", "Standup", 9, 9, minute_end=30)]
    text = render_day(events, ANCHOR, "cal", tz=UTC)
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert plan.is_empty()


def test_diff_create_new_line() -> None:
    events = [_evt("abc", "Standup", 9, 9, minute_end=30)]
    text = render_day(events, ANCHOR, "cal", tz=UTC) + "10:00-11:00  New event\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert len(plan.creates) == 1
    assert plan.creates[0].parsed.title == "New event"
    assert plan.updates == []
    assert plan.deletes == []


def test_diff_update_title() -> None:
    events = [_evt("abc", "Standup", 9, 9, minute_end=30)]
    text = "09:00-09:30  Daily standup  # id:abc\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert len(plan.updates) == 1
    assert "title" in plan.updates[0].changes[0]


def test_diff_update_time() -> None:
    events = [_evt("abc", "Standup", 9, 9, minute_end=30)]
    text = "10:00-10:30  Standup  # id:abc\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert len(plan.updates) == 1
    change_str = " | ".join(plan.updates[0].changes)
    assert "start" in change_str
    assert "end" in change_str


def test_diff_update_location() -> None:
    events = [_evt("abc", "Sync", 10, 11)]
    text = "10:00-11:00  Sync @office  # id:abc\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert len(plan.updates) == 1
    assert any("location" in c for c in plan.updates[0].changes)


def test_diff_update_attendees_added() -> None:
    events = [_evt("abc", "Sync", 10, 11)]
    text = "10:00-11:00  Sync +invite:alice@x.com  # id:abc\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert len(plan.updates) == 1
    assert any("attendees" in c for c in plan.updates[0].changes)


def test_diff_update_attendees_removed() -> None:
    events = [_evt("abc", "Sync", 10, 11, attendees=("a@x.com",))]
    text = "10:00-11:00  Sync  # id:abc\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert len(plan.updates) == 1
    assert any("attendees" in c for c in plan.updates[0].changes)


def test_diff_attendees_order_and_case_insensitive() -> None:
    events = [_evt("abc", "Sync", 10, 11, attendees=("a@x.com", "B@x.com"))]
    text = "10:00-11:00  Sync +invite:b@x.com +invite:A@x.com  # id:abc\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert plan.is_empty()


def test_diff_update_meet_added() -> None:
    events = [_evt("abc", "Sync", 10, 11)]
    text = "10:00-11:00  Sync +meet  # id:abc\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert len(plan.updates) == 1
    assert any("meet" in c for c in plan.updates[0].changes)


def test_diff_delete_missing_id() -> None:
    events = [
        _evt("abc", "Standup", 9, 9, minute_end=30),
        _evt("def", "Lunch", 12, 13),
    ]
    text = "09:00-09:30  Standup  # id:abc\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert len(plan.deletes) == 1
    assert plan.deletes[0].event.id == "def"


def test_diff_combined() -> None:
    events = [
        _evt("a", "Standup", 9, 9, minute_end=30),
        _evt("b", "Deep work", 9, 11, minute_start=30),
        _evt("c", "Lunch", 12, 13),
    ]
    text = (
        "09:00-09:30  Standup  # id:a\n"
        "09:30-11:30  Deeper work  # id:b\n"
        "17:30-18:30  Gym\n"
    )
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    assert len(plan.creates) == 1
    assert len(plan.updates) == 1
    assert len(plan.deletes) == 1


def test_format_plan_human_readable() -> None:
    events = [_evt("abc", "Standup", 9, 9, minute_end=30)]
    text = "09:00-09:30  Daily standup  # id:abc\n10:00-11:00  New thing\n"
    parsed, _ = parse_dayfile(text, ANCHOR, tz=UTC)
    plan = diff_plan(events, parsed)
    rendered = format_plan(plan)
    assert "update" in rendered
    assert "create" in rendered
    assert "Daily standup" in rendered
    assert "New thing" in rendered


# Week view --------------------------------------------------------------


def test_render_week_groups_by_day() -> None:
    mon = date(2026, 5, 11)
    tue = date(2026, 5, 12)
    events_by_day = {
        mon: [_evt("a", "1:1 with Alex", 9, 10, day=mon)],
        tue: [_evt("b", "Standup", 9, 9, minute_end=30, day=tue)],
    }
    text = render_week(events_by_day, "cal", tz=UTC)
    assert "# Monday" in text
    assert "# Tuesday" in text
    assert "1:1 with Alex" in text
    assert "Standup" in text


def test_week_round_trip_keeps_per_day_assignment() -> None:
    mon = date(2026, 5, 11)
    tue = date(2026, 5, 12)
    events_by_day = {
        mon: [_evt("a", "1:1 with Alex", 9, 10, day=mon)],
        tue: [_evt("b", "Standup", 9, 9, minute_end=30, day=tue)],
    }
    text = render_week(events_by_day, "cal", tz=UTC)
    parsed, errors = parse_dayfile(text, mon, tz=UTC)
    assert errors == []
    assert len(parsed) == 2
    by_id = {p.source_id: p for p in parsed}
    assert by_id["a"].start.date() == mon
    assert by_id["b"].start.date() == tue


def test_week_cross_day_move_via_diff() -> None:
    """Moving a line from Monday's section to Wednesday's becomes an update with new date."""
    mon = date(2026, 5, 11)
    tue = date(2026, 5, 12)
    monday_event = _evt("a", "Code review", 14, 15, day=mon)
    tuesday_event = _evt("b", "Standup", 9, 9, minute_end=30, day=tue)
    events = [monday_event, tuesday_event]

    text = (
        "# Monday May 11\n"
        "\n"
        "# Tuesday May 12\n"
        "09:00-09:30  Standup  # id:b\n"
        "\n"
        "# Wednesday May 13\n"
        "14:00-15:00  Code review  # id:a\n"
    )
    parsed, errors = parse_dayfile(text, mon, tz=UTC)
    assert errors == []
    plan = diff_plan(events, parsed)
    assert len(plan.updates) == 1
    upd = plan.updates[0]
    assert upd.event.id == "a"
    assert upd.parsed.start.date() == date(2026, 5, 13)
    assert plan.creates == []
    assert plan.deletes == []

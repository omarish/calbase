from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from calbase.time_utils import (
    parse_day_spec,
    parse_from,
    parse_last,
    parse_when,
)


def test_parse_last_hours_and_minutes() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    result = parse_last("1h30m", now)
    assert result.start == now - timedelta(hours=1, minutes=30)
    assert result.end == now


def test_parse_last_minutes_only() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    result = parse_last("45m", now)
    assert result.start == now - timedelta(minutes=45)
    assert result.end == now


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0m",
        "0h",
        "abc",
    ],
)
def test_parse_last_invalid(value: str) -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        parse_last(value, now)


@pytest.mark.parametrize(
    ("value", "expected_start_delta", "expected_end_delta"),
    [
        ("9am-12pm", timedelta(hours=9), timedelta(hours=12)),
        ("6pm-8pm", timedelta(hours=18), timedelta(hours=20)),
        ("6pm-8pm yesterday", timedelta(days=-1, hours=18), timedelta(days=-1, hours=20)),
        ("Yesterday 6pm-8pm", timedelta(days=-1, hours=18), timedelta(days=-1, hours=20)),
        ("11pm-1am", timedelta(hours=23), timedelta(days=1, hours=1)),
        ("9am to 12pm", timedelta(hours=9), timedelta(hours=12)),
        ("7:45am-9:15a", timedelta(hours=7, minutes=45), timedelta(hours=9, minutes=15)),
        ("9:15a-9:30p", timedelta(hours=9, minutes=15), timedelta(hours=21, minutes=30)),
        ("14:00-16:00", timedelta(hours=14), timedelta(hours=16)),
    ],
)
def test_parse_from_relative(
    value: str, expected_start_delta: timedelta, expected_end_delta: timedelta
) -> None:
    now = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
    result = parse_from(value, now)
    expected_start = now + expected_start_delta
    expected_end = now + expected_end_delta
    assert result.start == expected_start
    assert result.end == expected_end
    assert result.start.tzinfo == timezone.utc
    assert result.end.tzinfo == timezone.utc


def test_parse_from_explicit_date() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    value = "2025-02-05 2pm-5pm"
    result = parse_from(value, now)
    expected_date = datetime(2025, 2, 5, tzinfo=timezone.utc).date()
    assert result.start.date() == expected_date
    assert result.end.date() == expected_date
    assert result.start.hour == 14
    assert result.end.hour == 17


@pytest.mark.parametrize("value", ["9am", "12pm-9am"])
def test_parse_from_invalid_inputs(value: str) -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    if "-" not in value and " to " not in value:
        with pytest.raises(ValueError):
            parse_from(value, now)


def test_parse_explicit_inverted() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        parse_from("Tomorrow 9am - Today 9am", now)


def test_parse_from_bare_digits() -> None:
    now = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
    result = parse_from("9-12", now)
    assert result.start == now + timedelta(hours=9)
    assert result.end == now + timedelta(hours=12)


# parse_when --------------------------------------------------------------


def test_parse_when_duration() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    tr, rest = parse_when(["4h", "Office", "cleanup"], now)
    assert tr.start == now - timedelta(hours=4)
    assert tr.end == now
    assert rest == ["Office", "cleanup"]


def test_parse_when_duration_only_no_title() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    tr, rest = parse_when(["1h30m"], now)
    assert tr.start == now - timedelta(hours=1, minutes=30)
    assert rest == []


def test_parse_when_today_range() -> None:
    now = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
    tr, rest = parse_when(["9a-12p", "Deep", "work"], now)
    assert tr.start == now + timedelta(hours=9)
    assert tr.end == now + timedelta(hours=12)
    assert rest == ["Deep", "work"]


def test_parse_when_bare_digit_range() -> None:
    now = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
    tr, rest = parse_when(["9-12", "Title"], now)
    assert tr.start == now + timedelta(hours=9)
    assert tr.end == now + timedelta(hours=12)
    assert rest == ["Title"]


def test_parse_when_day_prefix() -> None:
    now = datetime(2026, 1, 14, 12, 0, tzinfo=timezone.utc)
    tr, rest = parse_when(["yesterday", "6p-8p", "Cooking"], now)
    assert tr.start.date() == date(2026, 1, 13)
    assert tr.end.date() == date(2026, 1, 13)
    assert tr.start.hour == 18
    assert tr.end.hour == 20
    assert rest == ["Cooking"]


def test_parse_when_iso_prefix() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    tr, rest = parse_when(["2025-02-05", "2pm-5pm", "Architecture", "review"], now)
    assert tr.start.date() == date(2025, 2, 5)
    assert tr.start.hour == 14
    assert tr.end.hour == 17
    assert rest == ["Architecture", "review"]


def test_parse_when_empty_raises() -> None:
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        parse_when([], now)


def test_parse_when_unparseable_first_token_raises() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        parse_when(["banana", "9-12"], now)


def test_parse_when_title_preserved_verbatim() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    tr, rest = parse_when(
        ["4h", "Home", "Office", "Cleanup", "and", "Re-Organization"], now
    )
    assert tr.end == now
    assert rest == ["Home", "Office", "Cleanup", "and", "Re-Organization"]


# parse_day_spec ---------------------------------------------------------


def test_parse_day_spec_today() -> None:
    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    assert parse_day_spec("today", now) == (date(2026, 5, 7), date(2026, 5, 7))


def test_parse_day_spec_yesterday_tomorrow() -> None:
    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    assert parse_day_spec("yesterday", now) == (date(2026, 5, 6), date(2026, 5, 6))
    assert parse_day_spec("tomorrow", now) == (date(2026, 5, 8), date(2026, 5, 8))


def test_parse_day_spec_iso() -> None:
    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    assert parse_day_spec("2026-05-10", now) == (date(2026, 5, 10), date(2026, 5, 10))


def test_parse_day_spec_weekday_upcoming() -> None:
    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    assert parse_day_spec("fri", now) == (date(2026, 5, 8), date(2026, 5, 8))
    assert parse_day_spec("monday", now) == (date(2026, 5, 11), date(2026, 5, 11))
    assert parse_day_spec("thu", now) == (date(2026, 5, 7), date(2026, 5, 7))


def test_parse_day_spec_week() -> None:
    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    assert parse_day_spec("week", now) == (date(2026, 5, 4), date(2026, 5, 10))
    assert parse_day_spec("next week", now) == (date(2026, 5, 11), date(2026, 5, 17))


def test_parse_day_spec_range() -> None:
    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    assert parse_day_spec("2026-05-10..2026-05-14", now) == (
        date(2026, 5, 10),
        date(2026, 5, 14),
    )
    assert parse_day_spec("5/10..5/14", now) == (date(2026, 5, 10), date(2026, 5, 14))


def test_parse_day_spec_invalid() -> None:
    now = datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        parse_day_spec("banana", now)
    with pytest.raises(ValueError):
        parse_day_spec("", now)

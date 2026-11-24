from __future__ import annotations

import pytest

from calbase.recurrence import (
    format_recurrence_display,
    parse_repeat,
    parse_rrule,
)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("daily", "RRULE:FREQ=DAILY"),
        ("DAILY", "RRULE:FREQ=DAILY"),
        ("weekdays", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("weekday", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("business", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("workdays", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("weekends", "RRULE:FREQ=WEEKLY;BYDAY=SA,SU"),
        ("weekly", "RRULE:FREQ=WEEKLY"),
        ("biweekly", "RRULE:FREQ=WEEKLY;INTERVAL=2"),
        ("fortnightly", "RRULE:FREQ=WEEKLY;INTERVAL=2"),
        ("monthly", "RRULE:FREQ=MONTHLY"),
        ("yearly", "RRULE:FREQ=YEARLY"),
        ("annually", "RRULE:FREQ=YEARLY"),
        ("1d", "RRULE:FREQ=DAILY"),
        ("2d", "RRULE:FREQ=DAILY;INTERVAL=2"),
        ("2w", "RRULE:FREQ=WEEKLY;INTERVAL=2"),
        ("3months", "RRULE:FREQ=MONTHLY;INTERVAL=3"),
        ("2y", "RRULE:FREQ=YEARLY;INTERVAL=2"),
        ("mon-fri", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("Mon-Fri", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("sat-sun", "RRULE:FREQ=WEEKLY;BYDAY=SA,SU"),
        ("fri-mon", "RRULE:FREQ=WEEKLY;BYDAY=FR,SA,SU,MO"),
        ("mon,wed,fri", "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"),
        ("tu,th", "RRULE:FREQ=WEEKLY;BYDAY=TU,TH"),
        ("tue,thu", "RRULE:FREQ=WEEKLY;BYDAY=TU,TH"),
        ("mon-wed,fri", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,FR"),
        ("fri", "RRULE:FREQ=WEEKLY;BYDAY=FR"),
        ("mon,mon,wed", "RRULE:FREQ=WEEKLY;BYDAY=MO,WE"),
    ],
)
def test_parse_repeat_valid(spec: str, expected: str) -> None:
    assert parse_repeat(spec) == expected


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "0w", "0d", "sometimes", "mon-wed-fri", "mon,", ",mon", "mon-foo"],
)
def test_parse_repeat_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_repeat(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FREQ=DAILY", "RRULE:FREQ=DAILY"),
        ("RRULE:FREQ=DAILY", "RRULE:FREQ=DAILY"),
        (
            "freq=weekly;byday=mo,tu,we,th,fr",
            "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        ),
        (
            '"FREQ=MONTHLY;BYDAY=1MO;COUNT=6"',
            "RRULE:FREQ=MONTHLY;BYDAY=1MO;COUNT=6",
        ),
        (
            "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH;UNTIL=20261231T235959Z;",
            "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH;UNTIL=20261231T235959Z",
        ),
    ],
)
def test_parse_rrule_valid(raw: str, expected: str) -> None:
    assert parse_rrule(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "RRULE:",
        "BYDAY=MO,WE,FR",
        "FREQ=QUARTERLY",
        "FREQ=DAILY;BADSEGMENT",
        "FREQ=DAILY;COUNT=",
        "FREQ=DAILY;BYDAY=MO WE",
    ],
)
def test_parse_rrule_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_rrule(bad)


@pytest.mark.parametrize(
    ("rrule", "display"),
    [
        ("RRULE:FREQ=DAILY", "+repeat:daily"),
        ("RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "+repeat:weekdays"),
        ("RRULE:FREQ=WEEKLY;BYDAY=SA,SU", "+repeat:weekends"),
        ("RRULE:FREQ=WEEKLY", "+repeat:weekly"),
        ("RRULE:FREQ=WEEKLY;INTERVAL=2", "+repeat:biweekly"),
        ("RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR", "+repeat:mon,wed,fri"),
        ("RRULE:FREQ=MONTHLY;BYDAY=1MO;COUNT=6", "+rrule:FREQ=MONTHLY;BYDAY=1MO;COUNT=6"),
    ],
)
def test_format_recurrence_display(rrule: str, display: str) -> None:
    assert format_recurrence_display(rrule) == display
    if display.startswith("+repeat:"):
        assert parse_repeat(display[len("+repeat:") :]) == rrule
    else:
        assert parse_rrule(display[len("+rrule:") :]) == rrule

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import dateparser

_DURATION_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*$", re.I)


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime


def parse_last(value: str, now: datetime) -> TimeRange:
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError("Invalid --last value. Expected formats like 4h, 30m, 1h30m.")

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    if hours == 0 and minutes == 0:
        raise ValueError("Invalid --last value. Duration cannot be zero.")

    delta = timedelta(hours=hours, minutes=minutes)
    return TimeRange(start=now - delta, end=now)


def _is_time_only(text: str) -> bool:
    return bool(re.match(r"^\d{1,2}(:\d{2})?\s*([ap]\.?m?\.?)?$", text, re.IGNORECASE))


def parse_from(value: str, now: datetime) -> TimeRange:
    value = value.strip()

    date_map = {}

    def replace_date(match):
        key = f"__DATE_{len(date_map)}__"
        date_map[key] = match.group(0)
        return key

    masked_value = re.sub(r"\d{4}-\d{2}-\d{2}", replace_date, value)
    masked_value = re.sub(r"(\d)\s*([ap])\b", r"\1\2m", masked_value, flags=re.IGNORECASE)

    parts = re.split(r"\s+to\s+|\s*-\s*", masked_value, maxsplit=1)
    if len(parts) != 2:
        raise ValueError(
            "Invalid --time/--from value. Expected range like '9am-12pm' or '6pm to 8pm'."
        )

    start_str_masked, end_str_masked = parts[0].strip(), parts[1].strip()

    start_str = start_str_masked
    end_str = end_str_masked
    for key, original in date_map.items():
        start_str = start_str.replace(key, original)
        end_str = end_str.replace(key, original)

    # Pad bare-digit hour-only sides ("9", "12") so dateparser treats them as times,
    # not as ambiguous month/day numbers. Only applied when the side is JUST digits.
    start_str = _pad_bare_hour(start_str)
    end_str = _pad_bare_hour(end_str)

    start_is_time_only = _is_time_only(start_str)
    end_is_time_only = _is_time_only(end_str)

    settings = {"RELATIVE_BASE": now}
    start_dt = dateparser.parse(start_str, settings=settings)
    end_dt = dateparser.parse(end_str, settings=settings)

    if not start_dt:
        raise ValueError(f"Could not parse start time: {start_str!r}")
    if not end_dt:
        raise ValueError(f"Could not parse end time: {end_str!r}")

    if start_dt.tzinfo is None and now.tzinfo:
        start_dt = start_dt.replace(tzinfo=now.tzinfo)
    if end_dt.tzinfo is None and now.tzinfo:
        end_dt = end_dt.replace(tzinfo=now.tzinfo)

    if not start_is_time_only and end_is_time_only:
        end_dt = end_dt.replace(year=start_dt.year, month=start_dt.month, day=start_dt.day)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

    elif start_is_time_only and not end_is_time_only:
        start_dt = start_dt.replace(year=end_dt.year, month=end_dt.month, day=end_dt.day)
        if end_dt <= start_dt:
            pass

    if end_dt <= start_dt:
        if start_is_time_only and end_is_time_only:
            end_next_day = end_dt + timedelta(days=1)
            if start_dt < end_next_day:
                end_dt = end_next_day

    if end_dt <= start_dt:
        raise ValueError(
            f"Invalid range: End time ({end_dt}) must be after start time ({start_dt})."
        )

    return TimeRange(start=start_dt, end=end_dt)


def _pad_bare_hour(text: str) -> str:
    """`9` -> `9:00` so dateparser treats it as a time, not a date number."""
    if re.fullmatch(r"\d{1,2}", text.strip()):
        return f"{text.strip()}:00"
    return text


# Token classification for parse_when
_RANGE_RE = re.compile(
    r"^\d{1,2}(?::\d{2})?\s*[ap]?\.?m?\.?\s*-\s*\d{1,2}(?::\d{2})?\s*[ap]?\.?m?\.?$",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAY_WORDS = {
    "today", "tonight", "yesterday", "tomorrow",
    "mon", "monday",
    "tue", "tues", "tuesday",
    "wed", "weds", "wednesday",
    "thu", "thur", "thurs", "thursday",
    "fri", "friday",
    "sat", "saturday",
    "sun", "sunday",
}


def _looks_like_duration(token: str) -> bool:
    return bool(re.fullmatch(r"\d+h(?:\d+m)?|\d+m", token, re.IGNORECASE))


def _looks_like_range(token: str) -> bool:
    if "-" not in token:
        return False
    return bool(_RANGE_RE.match(token))


def parse_when(tokens: list[str], now: datetime) -> tuple[TimeRange, list[str]]:
    """Eat time tokens from the left of `tokens`; return (TimeRange, remaining_tokens).

    Recognized prefixes:
      duration (1 token):       4h, 30m, 1h30m
      today range (1 token):    9a-12p, 9-12, 14:00-16:00
      day + range (2 tokens):   sat 8p-11p, tomorrow 9-10a, yesterday 6p-8p
      ISO + range (2 tokens):   2026-05-15 9a-5p
    """
    if not tokens:
        raise ValueError(
            "Missing time spec. Examples: 4h, 9a-12p, sat 8p-11p, 2026-05-15 9a-5p."
        )

    first = tokens[0]
    rest = tokens[1:]

    if _looks_like_duration(first):
        return parse_last(first, now), rest

    if _ISO_DATE_RE.match(first) and rest and _looks_like_range(rest[0]):
        return parse_from(f"{first} {rest[0]}", now), rest[1:]

    if first.lower() in _DAY_WORDS and rest and _looks_like_range(rest[0]):
        return parse_from(f"{first} {rest[0]}", now), rest[1:]

    if _looks_like_range(first):
        return parse_from(first, now), rest

    raise ValueError(
        f"Could not parse time spec from {first!r}. "
        "Examples: 4h, 9a-12p, sat 8p-11p, 2026-05-15 9a-5p."
    )


_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def parse_day_spec(value: str, now: datetime) -> tuple[date, date]:
    """Parse a day spec for `cb edit <when>`.

    Accepts: today, yesterday, tomorrow, weekday names, ISO date,
    week, next week, MM/DD, MM/DD..MM/DD, ISO..ISO.
    Returns (start_date, end_date), inclusive on both ends.
    """
    raw = value.strip()
    if not raw:
        raise ValueError("Missing day spec.")

    today = now.date()
    lowered = raw.lower()

    if lowered == "today":
        return today, today
    if lowered == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)
    if lowered == "tomorrow":
        return today + timedelta(days=1), today + timedelta(days=1)
    if lowered in {"week", "this week"}:
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if lowered == "next week":
        start = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return start, start + timedelta(days=6)
    if lowered == "last week":
        start = today - timedelta(days=today.weekday()) - timedelta(days=7)
        return start, start + timedelta(days=6)

    if ".." in raw:
        a_str, b_str = raw.split("..", 1)
        a, _ = parse_day_spec(a_str, now)
        _, b = parse_day_spec(b_str, now)
        if b < a:
            raise ValueError(f"Day range end ({b}) is before start ({a}).")
        return a, b

    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if iso:
        d = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        return d, d

    md = re.fullmatch(r"(\d{1,2})/(\d{1,2})", raw)
    if md:
        d = date(today.year, int(md.group(1)), int(md.group(2)))
        return d, d

    if lowered in _WEEKDAYS:
        target = _WEEKDAYS[lowered]
        days_ahead = (target - today.weekday()) % 7
        d = today + timedelta(days=days_ahead)
        return d, d

    raise ValueError(
        f"Could not parse day spec {raw!r}. "
        "Try: today, yesterday, tomorrow, fri, 2026-05-10, week, next week, 5/10..5/14."
    )

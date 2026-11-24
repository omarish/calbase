"""Recurrence parsing and formatting for Google Calendar RRULEs.

Pure functions with no external dependencies, shared by quick capture (`cb ...`)
and interactive dayfile editing (`cb edit ...`).
"""

from __future__ import annotations

import re

_PRESETS: dict[str, str] = {
    "daily": "RRULE:FREQ=DAILY",
    "weekdays": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekday": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "business": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "workdays": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "workday": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekends": "RRULE:FREQ=WEEKLY;BYDAY=SA,SU",
    "weekend": "RRULE:FREQ=WEEKLY;BYDAY=SA,SU",
    "weekly": "RRULE:FREQ=WEEKLY",
    "biweekly": "RRULE:FREQ=WEEKLY;INTERVAL=2",
    "fortnightly": "RRULE:FREQ=WEEKLY;INTERVAL=2",
    "monthly": "RRULE:FREQ=MONTHLY",
    "yearly": "RRULE:FREQ=YEARLY",
    "annually": "RRULE:FREQ=YEARLY",
}

_CANONICAL_DISPLAY: dict[str, str] = {
    "RRULE:FREQ=DAILY": "+repeat:daily",
    "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR": "+repeat:weekdays",
    "RRULE:FREQ=WEEKLY;BYDAY=SA,SU": "+repeat:weekends",
    "RRULE:FREQ=WEEKLY": "+repeat:weekly",
    "RRULE:FREQ=WEEKLY;INTERVAL=2": "+repeat:biweekly",
    "RRULE:FREQ=MONTHLY": "+repeat:monthly",
    "RRULE:FREQ=YEARLY": "+repeat:yearly",
}

_DAY_ORDER = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_DAY_CODE_TO_SHORT = {
    "MO": "mon",
    "TU": "tue",
    "WE": "wed",
    "TH": "thu",
    "FR": "fri",
    "SA": "sat",
    "SU": "sun",
}
_DAY_ALIASES: dict[str, str] = {
    "mo": "MO", "mon": "MO", "monday": "MO",
    "tu": "TU", "tue": "TU", "tues": "TU", "tuesday": "TU",
    "we": "WE", "wed": "WE", "weds": "WE", "wednesday": "WE",
    "th": "TH", "thu": "TH", "thur": "TH", "thurs": "TH", "thursday": "TH",
    "fr": "FR", "fri": "FR", "friday": "FR",
    "sa": "SA", "sat": "SA", "saturday": "SA",
    "su": "SU", "sun": "SU", "sunday": "SU",
}

_INTERVAL_RE = re.compile(
    r"^(\d+)(d|day|days|w|wk|wks|week|weeks|m|mo|mos|month|months|y|yr|yrs|year|years)$",
    re.IGNORECASE,
)
_UNIT_TO_FREQ = {
    "d": "DAILY", "day": "DAILY", "days": "DAILY",
    "w": "WEEKLY", "wk": "WEEKLY", "wks": "WEEKLY", "week": "WEEKLY", "weeks": "WEEKLY",
    "m": "MONTHLY", "mo": "MONTHLY", "mos": "MONTHLY", "month": "MONTHLY", "months": "MONTHLY",
    "y": "YEARLY", "yr": "YEARLY", "yrs": "YEARLY", "year": "YEARLY", "years": "YEARLY",
}

_VALID_FREQS = {
    "DAILY",
    "WEEKLY",
    "MONTHLY",
    "YEARLY",
    "HOURLY",
    "MINUTELY",
    "SECONDLY",
}
_RRULE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9-]*$")
_WEEKLY_BYDAY_RE = re.compile(r"^RRULE:FREQ=WEEKLY;BYDAY=((?:MO|TU|WE|TH|FR|SA|SU)(?:,(?:MO|TU|WE|TH|FR|SA|SU))*)$")


def parse_repeat(spec: str) -> str:
    """Parse a `+repeat:<spec>` shorthand into an RFC 5545 `RRULE:...` string.

    Accepted forms (case-insensitive):
      - Presets: daily, weekdays (or business/workdays), weekends, weekly,
        biweekly, monthly, yearly
      - Intervals: 2d, 2w, 3m, 2y
      - Weekday lists/ranges: mon-fri, mon,wed,fri, tue,thu, fri
    """
    cleaned = spec.strip().lower()
    if not cleaned:
        raise ValueError(
            "Empty +repeat value. Try: daily, weekdays, weekly, biweekly, monthly, "
            "mon-fri, mon,wed,fri, or 2w."
        )

    if cleaned in _PRESETS:
        return _PRESETS[cleaned]

    interval_match = _INTERVAL_RE.match(cleaned)
    if interval_match:
        count = int(interval_match.group(1))
        if count < 1:
            raise ValueError(f"Invalid +repeat interval {spec!r}: must be >= 1.")
        freq = _UNIT_TO_FREQ[interval_match.group(2).lower()]
        if count == 1:
            return f"RRULE:FREQ={freq}"
        return f"RRULE:FREQ={freq};INTERVAL={count}"

    days = _parse_day_list(cleaned)
    if days is not None:
        return f"RRULE:FREQ=WEEKLY;BYDAY={','.join(days)}"

    raise ValueError(
        f"Unknown +repeat value {spec!r}. "
        "Try: daily, weekdays, weekends, weekly, biweekly, monthly, yearly, "
        "mon-fri, mon,wed,fri, 2w — or use +rrule:FREQ=..."
    )


def _parse_day_list(text: str) -> list[str] | None:
    parts = [p.strip() for p in text.split(",")]
    if not parts or any(not p for p in parts):
        return None

    days: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2:
                return None
            start_code = _DAY_ALIASES.get(bounds[0].strip())
            end_code = _DAY_ALIASES.get(bounds[1].strip())
            if not start_code or not end_code:
                return None
            idx = _DAY_ORDER.index(start_code)
            end_idx = _DAY_ORDER.index(end_code)
            while True:
                code = _DAY_ORDER[idx]
                if code not in seen:
                    seen.add(code)
                    days.append(code)
                if idx == end_idx:
                    break
                idx = (idx + 1) % 7
        else:
            code = _DAY_ALIASES.get(part)
            if not code:
                return None
            if code not in seen:
                seen.add(code)
                days.append(code)

    return days or None


def parse_rrule(raw: str) -> str:
    """Validate and normalize a raw `+rrule:<rule>` into `RRULE:...`.

    Accepts with or without a leading `RRULE:` prefix, and strips optional
    surrounding quotes.
    """
    cleaned = raw.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        raise ValueError(
            "Empty +rrule value. Example: +rrule:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
        )

    if cleaned.upper().startswith("RRULE:"):
        cleaned = cleaned[len("RRULE:") :].strip()

    cleaned = cleaned.rstrip(";")
    if not cleaned:
        raise ValueError(
            "Empty +rrule value. Example: +rrule:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
        )

    parts = cleaned.split(";")
    normalized_parts: list[str] = []
    freq_value: str | None = None

    for part in parts:
        if "=" not in part:
            raise ValueError(
                f"Invalid +rrule segment {part!r} in {raw!r}; expected KEY=VALUE pairs."
            )
        key_raw, val_raw = part.split("=", 1)
        key = key_raw.strip().upper()
        val = val_raw.strip().upper()
        if not _RRULE_KEY_RE.match(key) or not val or any(ch.isspace() for ch in val):
            raise ValueError(
                f"Invalid +rrule segment {part!r} in {raw!r}; expected KEY=VALUE."
            )
        if key == "FREQ":
            freq_value = val
        normalized_parts.append(f"{key}={val}")

    if freq_value is None:
        raise ValueError(
            f"Invalid +rrule {raw!r}: missing required FREQ=... (e.g. FREQ=WEEKLY;BYDAY=MO,WE,FR)."
        )
    if freq_value not in _VALID_FREQS:
        raise ValueError(
            f"Invalid +rrule FREQ={freq_value!r}; expected one of {', '.join(sorted(_VALID_FREQS))}."
        )

    return f"RRULE:{';'.join(normalized_parts)}"


def format_recurrence_display(rrule: str) -> str:
    """Format an `RRULE:...` string back to a compact `+repeat:` or `+rrule:` token."""
    if rrule in _CANONICAL_DISPLAY:
        return _CANONICAL_DISPLAY[rrule]

    byday_match = _WEEKLY_BYDAY_RE.match(rrule)
    if byday_match:
        short_days = [_DAY_CODE_TO_SHORT[code] for code in byday_match.group(1).split(",")]
        return f"+repeat:{','.join(short_days)}"

    body = rrule[len("RRULE:") :] if rrule.startswith("RRULE:") else rrule
    return f"+rrule:{body}"

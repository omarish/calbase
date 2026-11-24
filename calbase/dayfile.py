"""Dayfile: text representation of a day (or week) of calendar events.

Used by `cb edit <when>` to round-trip events through `$EDITOR`. Pure functions —
no Google API dependency, easy to unit-test.

Line grammar:
    HH:MM-HH:MM  Title [@location] [+meet] [+invite:email] [+repeat:spec|+rrule:rule]  [# id:<id>]

The `# id:<id>` suffix is the rebase-style tracking handle used by `diff_plan` to
match parsed lines back to their original events.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Optional

from calbase.recurrence import (
    format_recurrence_display,
    parse_repeat,
    parse_rrule,
)


# Data model -------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """A calendar event, as we model it for dayfile rendering and diffing."""

    id: str
    title: str
    start: datetime
    end: datetime
    location: Optional[str] = None
    attendees: tuple[str, ...] = ()
    meet: bool = False
    description: Optional[str] = None
    recurrence: Optional[str] = None


@dataclass
class ParsedLine:
    line_no: int
    start: datetime
    end: datetime
    title: str
    location: Optional[str] = None
    meet: bool = False
    invites: list[str] = field(default_factory=list)
    source_id: Optional[str] = None
    recurrence: Optional[str] = None


@dataclass
class ParseError:
    line_no: int
    message: str
    raw: str


@dataclass
class CreateOp:
    parsed: ParsedLine


@dataclass
class UpdateOp:
    event: Event
    parsed: ParsedLine
    changes: list[str]


@dataclass
class DeleteOp:
    event: Event


@dataclass
class Plan:
    creates: list[CreateOp] = field(default_factory=list)
    updates: list[UpdateOp] = field(default_factory=list)
    deletes: list[DeleteOp] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.creates or self.updates or self.deletes)


# Render -----------------------------------------------------------------


_DAY_HEADER_FMT = "%A %B %-d"
_DAY_HEADER_FULL_FMT = "%A, %B %-d, %Y"


def _short_id_map(events: list[Event]) -> dict[str, str]:
    """Return {event.id: display_id} with git-style short handles for long IDs.

    Short IDs (<= 8 chars, e.g. in tests) are kept verbatim. Long Google
    Calendar IDs (26-45+ chars) are shortened to a 6-char deterministic hex
    handle (extended if needed to stay unique within the buffer).
    """
    ids = list(dict.fromkeys(e.id for e in events if e.id))
    short: dict[str, str] = {}
    used: set[str] = set()

    for eid in ids:
        if len(eid) <= 8:
            short[eid] = eid
            used.add(eid)

    long_ids = [eid for eid in ids if len(eid) > 8]
    if not long_ids:
        return short

    for width in range(6, 16):
        candidate: dict[str, str] = {}
        seen = set(used)
        collision = False
        for eid in long_ids:
            h = hashlib.sha1(eid.encode("utf-8")).hexdigest()[:width]
            if h in seen:
                collision = True
                break
            seen.add(h)
            candidate[eid] = h
        if not collision:
            short.update(candidate)
            return short

    for eid in long_ids:
        short[eid] = eid
    return short


_HEADER_HELP_LINES = [
    "# Edit, add, delete, reorder lines. Save to apply, quit-without-saving to abort.",
    '# Modifiers: @location (or @"multi word"), +meet, +invite:email (repeatable)',
    "# Repeat:    +repeat:daily|weekdays|weekly|biweekly|monthly|mon,wed,fri  or  +rrule:FREQ=...",
]


def render_day(
    events: list[Event],
    day: date,
    calendar_summary: str,
    tz: Optional[tzinfo] = None,
) -> str:
    """Render a single day's events as a dayfile.

    `tz` controls how event times are displayed. Defaults to local time.
    Empty days get a commented example hint so the format is obvious.
    """
    header = [
        f"# calbase edit — {day.strftime(_DAY_HEADER_FULL_FMT)} (calendar: {calendar_summary})",
        *_HEADER_HELP_LINES,
        "",
    ]
    id_map = _short_id_map(events)
    body = _render_event_lines(sorted(events, key=lambda e: e.start), tz, id_map=id_map)
    if not body:
        body = _empty_day_hint()
    return "\n".join(header + body) + "\n"


_EMPTY_HINT_LINES = [
    "# Your day is empty. Add lines like these to schedule events:",
    "#",
    "#   09:00-09:30  Standup",
    "#   12:00-13:00  Lunch",
    "#   14:00-15:00  Sync with Alex @office +meet +invite:alice@example.com",
]


def _empty_day_hint() -> list[str]:
    return list(_EMPTY_HINT_LINES)


def render_week(
    events_by_day: dict[date, list[Event]],
    calendar_summary: str,
    tz: Optional[tzinfo] = None,
) -> str:
    """Render multiple days as a week-style dayfile, with per-day headers."""
    days = sorted(events_by_day.keys())
    if not days:
        header = [
            f"# calbase edit (calendar: {calendar_summary})",
            *_HEADER_HELP_LINES,
            "",
        ]
        return "\n".join(header) + "\n"

    first, last = days[0], days[-1]
    title_line = (
        f"# calbase edit — {first.strftime('%a %b %-d')} – {last.strftime('%a %b %-d, %Y')}"
        f" (calendar: {calendar_summary})"
    )
    out: list[str] = [
        title_line,
        *_HEADER_HELP_LINES,
        "",
    ]
    all_events = [e for d in days for e in events_by_day.get(d, [])]
    id_map = _short_id_map(all_events)
    total_events = len(all_events)
    if total_events == 0:
        out.extend(_empty_day_hint())
        out.append("")
    for d in days:
        out.append(f"# {d.strftime(_DAY_HEADER_FMT)}")
        day_events = sorted(events_by_day.get(d, []), key=lambda e: e.start)
        out.extend(_render_event_lines(day_events, tz, id_map=id_map))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _render_event_lines(
    events: list[Event],
    tz: Optional[tzinfo],
    *,
    id_map: Optional[dict[str, str]] = None,
) -> list[str]:
    """Render a list of events as aligned dayfile lines (no headers)."""
    rendered = [_render_event_line_parts(e, tz, id_map=id_map) for e in events]
    if not rendered:
        return []
    pad_to = max(len(content) for content, _ in rendered)
    pad_to = max(pad_to, 46)
    out: list[str] = []
    for content, eid in rendered:
        if eid:
            out.append(f"{content:<{pad_to}}  # id:{eid}")
        else:
            out.append(content)
    return out


def _render_event_line_parts(
    event: Event,
    tz: Optional[tzinfo],
    *,
    id_map: Optional[dict[str, str]] = None,
) -> tuple[str, Optional[str]]:
    """Return (content_without_id_comment, id-or-None) for a single event."""
    start_disp = _to_display(event.start, tz)
    end_disp = _to_display(event.end, tz)
    time_str = (
        f"{start_disp.hour:02d}:{start_disp.minute:02d}"
        f"-{end_disp.hour:02d}:{end_disp.minute:02d}"
    )

    pieces: list[str] = [event.title.strip()]
    if event.location:
        loc = event.location.strip()
        if " " in loc or '"' in loc:
            pieces.append(f'@"{loc}"')
        else:
            pieces.append(f"@{loc}")
    if event.meet:
        pieces.append("+meet")
    for email in event.attendees:
        pieces.append(f"+invite:{email}")
    if event.recurrence:
        pieces.append(format_recurrence_display(event.recurrence))

    content = f"{time_str}  {' '.join(pieces)}"
    display_id = (id_map.get(event.id, event.id) if id_map else event.id) or None
    return content, display_id


def _to_display(dt: datetime, tz: Optional[tzinfo]) -> datetime:
    if dt.tzinfo is None:
        return dt
    if tz is None:
        return dt.astimezone()
    return dt.astimezone(tz)


# Parse ------------------------------------------------------------------


_TIME_PREFIX_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s+(.*)$"
)
_ID_TRAILER_RE = re.compile(r"\s+#\s*id:(\S+)\s*$")
_LOC_QUOTED_RE = re.compile(r'@"([^"]+)"')
_LOC_BARE_RE = re.compile(r'(?:^|\s)@([^\s@"]+)')
_MEET_RE = re.compile(r"(?:^|\s)\+meet\b", re.IGNORECASE)
_INVITE_RE = re.compile(r"(?:^|\s)\+invite:(\S+)", re.IGNORECASE)
_REPEAT_RE = re.compile(r"(?:^|\s)\+repeat:(\S*)", re.IGNORECASE)
_RRULE_QUOTED_RE = re.compile(r'(?:^|\s)\+rrule:"([^"]*)"', re.IGNORECASE)
_RRULE_BARE_RE = re.compile(r"(?:^|\s)\+rrule:(\S*)", re.IGNORECASE)


class _LineParseError(Exception):
    pass


def parse_dayfile(
    text: str,
    anchor_day: date,
    tz: Optional[tzinfo] = None,
) -> tuple[list[ParsedLine], list[ParseError]]:
    """Parse a dayfile.

    `anchor_day` is the day used for lines without a per-day header (i.e. the
    single-day case, or lines that appear before any header in week view).
    `tz` defaults to local time.
    """
    if tz is None:
        tz = datetime.now().astimezone().tzinfo

    parsed: list[ParsedLine] = []
    errors: list[ParseError] = []
    current_day = anchor_day

    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            day_match = _try_parse_day_header(stripped, anchor_day)
            if day_match is not None:
                current_day = day_match
            continue
        try:
            parsed.append(_parse_line(raw, i, current_day, tz))
        except _LineParseError as e:
            errors.append(ParseError(line_no=i, message=str(e), raw=raw))

    return parsed, errors


def _parse_line(raw: str, line_no: int, day: date, tz: tzinfo) -> ParsedLine:
    line = raw.rstrip()

    source_id: Optional[str] = None
    id_match = _ID_TRAILER_RE.search(line)
    if id_match:
        source_id = id_match.group(1)
        line = line[: id_match.start()].rstrip()

    time_match = _TIME_PREFIX_RE.match(line)
    if not time_match:
        raise _LineParseError(
            "expected `HH:MM-HH:MM  Title …` (e.g. `09:00-09:30  Standup`)"
        )

    sh, sm = int(time_match.group(1)), int(time_match.group(2))
    eh, em = int(time_match.group(3)), int(time_match.group(4))
    if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
        raise _LineParseError(f"invalid time of day in {raw.strip()!r}")

    rest = time_match.group(5).strip()
    if not rest:
        raise _LineParseError("missing title")

    location: Optional[str] = None
    loc_q = _LOC_QUOTED_RE.search(rest)
    if loc_q:
        location = loc_q.group(1)
        rest = (rest[: loc_q.start()] + rest[loc_q.end() :]).strip()
    else:
        loc_b = _LOC_BARE_RE.search(rest)
        if loc_b:
            location = loc_b.group(1)
            rest = (rest[: loc_b.start()] + rest[loc_b.end() :]).strip()

    meet = False
    while True:
        m = _MEET_RE.search(rest)
        if not m:
            break
        meet = True
        rest = (rest[: m.start()] + rest[m.end() :]).strip()

    invites: list[str] = []
    while True:
        m = _INVITE_RE.search(rest)
        if not m:
            break
        invites.append(m.group(1))
        rest = (rest[: m.start()] + rest[m.end() :]).strip()

    repeats: list[str] = []
    while True:
        m = _REPEAT_RE.search(rest)
        if not m:
            break
        repeats.append(m.group(1))
        rest = (rest[: m.start()] + rest[m.end() :]).strip()

    rrules: list[str] = []
    while True:
        m = _RRULE_QUOTED_RE.search(rest)
        if not m:
            break
        rrules.append(m.group(1))
        rest = (rest[: m.start()] + rest[m.end() :]).strip()
    while True:
        m = _RRULE_BARE_RE.search(rest)
        if not m:
            break
        rrules.append(m.group(1))
        rest = (rest[: m.start()] + rest[m.end() :]).strip()

    if repeats and rrules:
        raise _LineParseError("cannot combine +repeat: and +rrule: on the same line")
    if len(repeats) > 1:
        raise _LineParseError("specify only one +repeat: per line")
    if len(rrules) > 1:
        raise _LineParseError("specify only one +rrule: per line")

    recurrence: Optional[str] = None
    try:
        if repeats:
            recurrence = parse_repeat(repeats[0])
        elif rrules:
            recurrence = parse_rrule(rrules[0])
    except ValueError as e:
        raise _LineParseError(str(e)) from e

    title = re.sub(r"\s+", " ", rest).strip()
    if not title:
        raise _LineParseError("missing title")

    start_dt = datetime.combine(day, time(sh, sm), tzinfo=tz)
    end_dt = datetime.combine(day, time(eh, em), tzinfo=tz)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    return ParsedLine(
        line_no=line_no,
        start=start_dt,
        end=end_dt,
        title=title,
        location=location,
        meet=meet,
        invites=invites,
        source_id=source_id,
        recurrence=recurrence,
    )


def _try_parse_day_header(comment_line: str, anchor_day: date) -> Optional[date]:
    """If `# Monday May 12` (or similar), return that date; else None."""
    text = comment_line.lstrip("#").strip()
    if not text:
        return None
    lowered = text.lower()
    if (
        lowered.startswith("calbase edit")
        or lowered.startswith("cb edit")
        or lowered.startswith("edit,")
        or lowered.startswith("!!")
    ):
        return None

    iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            pass

    weekday_re = (
        r"(?:mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun"
        r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    )
    month_re = (
        r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
        r"|january|february|march|april|may|june|july|august"
        r"|september|october|november|december)"
    )
    m = re.match(
        rf"^(?:{weekday_re}\s*,?\s+)?({month_re})\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    month_num = _MONTH_NUM[m.group(1).lower()]
    day_num = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else anchor_day.year
    try:
        return date(year, month_num, day_num)
    except ValueError:
        return None


_MONTH_NUM = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


# Diff and apply plan -----------------------------------------------------


def diff_plan(original: list[Event], parsed: list[ParsedLine]) -> Plan:
    """Compute creates/updates/deletes by matching `# id:<id>` to original events."""
    by_id: dict[str, Event] = {e.id: e for e in original}
    for full_id, short_id in _short_id_map(original).items():
        by_id.setdefault(short_id, by_id[full_id])
    seen: set[str] = set()
    creates: list[CreateOp] = []
    updates: list[UpdateOp] = []

    for line in parsed:
        if line.source_id and line.source_id in by_id:
            ev = by_id[line.source_id]
            seen.add(ev.id)
            changes = _diff_event(ev, line)
            if changes:
                updates.append(UpdateOp(event=ev, parsed=line, changes=changes))
        else:
            creates.append(CreateOp(parsed=line))

    deletes = [DeleteOp(event=e) for e in original if e.id not in seen]
    return Plan(creates=creates, updates=updates, deletes=deletes)


def _diff_event(event: Event, line: ParsedLine) -> list[str]:
    changes: list[str] = []
    if event.title.strip() != line.title.strip():
        changes.append(f"title: {event.title!r} → {line.title!r}")
    start_changed = _norm_minute(event.start) != _norm_minute(line.start)
    end_changed = _norm_minute(event.end) != _norm_minute(line.end)
    if start_changed and end_changed and _is_pure_date_move(event.start, event.end, line.start, line.end):
        old_s = event.start.astimezone() if event.start.tzinfo else event.start
        new_s = line.start.astimezone() if line.start.tzinfo else line.start
        changes.append(f"date: {old_s.strftime('%b %-d')} → {new_s.strftime('%b %-d')}")
    else:
        if start_changed:
            changes.append(f"start: {_fmt_diff_pair(event.start, line.start)}")
        if end_changed:
            changes.append(f"end: {_fmt_diff_pair(event.end, line.end)}")
    if (event.location or "") != (line.location or ""):
        changes.append(f"location: {event.location!r} → {line.location!r}")
    if event.meet != line.meet:
        changes.append(f"meet: {event.meet} → {line.meet}")
    old_emails = _normalize_emails(event.attendees)
    new_emails = _normalize_emails(line.invites)
    if old_emails != new_emails:
        changes.append(f"attendees: {old_emails} → {new_emails}")
    if event.recurrence != line.recurrence:
        changes.append(
            f"repeat: {event.recurrence or 'none'} → {line.recurrence or 'none'}"
        )
    return changes


def _is_pure_date_move(
    old_start: datetime, old_end: datetime, new_start: datetime, new_end: datetime
) -> bool:
    os_ = old_start.astimezone() if old_start.tzinfo else old_start
    oe_ = old_end.astimezone() if old_end.tzinfo else old_end
    ns_ = new_start.astimezone() if new_start.tzinfo else new_start
    ne_ = new_end.astimezone() if new_end.tzinfo else new_end
    return (
        os_.date() != ns_.date()
        and (os_.hour, os_.minute) == (ns_.hour, ns_.minute)
        and (oe_.hour, oe_.minute) == (ne_.hour, ne_.minute)
    )


def _fmt_diff_pair(old: datetime, new: datetime) -> str:
    o = old.astimezone() if old.tzinfo else old
    n = new.astimezone() if new.tzinfo else new
    if o.date() == n.date():
        return f"{o.strftime('%H:%M')} → {n.strftime('%H:%M')}"
    return f"{o.strftime('%b %-d %H:%M')} → {n.strftime('%b %-d %H:%M')}"


def _normalize_emails(emails) -> list[str]:
    """Lower-case + dedupe + sort, for order-insensitive attendee comparison."""
    return sorted({e.strip().lower() for e in emails if e and e.strip()})


def _norm_minute(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(second=0, microsecond=0)
    return dt.astimezone().replace(second=0, microsecond=0)


def _fmt_dt(dt: datetime) -> str:
    local = dt.astimezone() if dt.tzinfo else dt
    return local.strftime("%Y-%m-%d %H:%M")


def _fmt_range(start: datetime, end: datetime) -> str:
    s = start.astimezone() if start.tzinfo else start
    e = end.astimezone() if end.tzinfo else end
    if s.date() == e.date():
        return f"{s.strftime('%Y-%m-%d %H:%M')}-{e.strftime('%H:%M')}"
    return f"{s.strftime('%Y-%m-%d %H:%M')}–{e.strftime('%Y-%m-%d %H:%M')}"


def format_plan(plan: Plan) -> str:
    """Render a human-readable preview of a Plan."""
    if plan.is_empty():
        return "No changes."

    lines: list[str] = []
    for op in plan.updates:
        change_str = ", ".join(op.changes)
        lines.append(
            f"  ~ update  {_fmt_range(op.parsed.start, op.parsed.end)}  "
            f"{op.parsed.title}    ({change_str})"
        )
    for op in plan.deletes:
        lines.append(
            f"  - delete  {_fmt_range(op.event.start, op.event.end)}  {op.event.title}"
        )
    for op in plan.creates:
        line = op.parsed
        bits = [line.title]
        if line.location:
            bits.append(f"@{line.location}")
        if line.meet:
            bits.append("+meet")
        for inv in line.invites:
            bits.append(f"+invite:{inv}")
        if line.recurrence:
            bits.append(format_recurrence_display(line.recurrence))
        lines.append(
            f"  + create  {_fmt_range(line.start, line.end)}  {' '.join(bits)}"
        )
    return "\n".join(lines)

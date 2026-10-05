"""RFC 5545 iCalendar serialization and interchange formatting.

Part of Phase 4B Generic Scheduling.
Implements:
- Zero heavy dependencies (native RFC 5545 generation and parsing).
- Strict UTC timestamp formatting (YYYYMMDDTHHMMSSZ).
- Safe escaping and unfolding according to RFC 5545 Section 3.1.
- VCALENDAR and VEVENT structure generation for published schedules.
"""

from datetime import UTC, datetime
from typing import Any

from app.integrations.calendar.dtos import CalendarEventDTO

PRODID: str = "-//IR Infotech//E7 Operations Engine Generic Scheduler//EN"


def _format_rfc5545_datetime(dt: datetime) -> str:
    """Format timezone-aware datetime into UTC RFC 5545 string: YYYYMMDDTHHMMSSZ."""
    utc_dt = dt.astimezone(UTC)
    return utc_dt.strftime("%Y%m%dT%H%M%SZ")


def _parse_rfc5545_datetime(dt_str: str) -> datetime:
    """Parse RFC 5545 UTC timestamp (YYYYMMDDTHHMMSSZ) to UTC datetime."""
    cleaned = dt_str.strip()
    if cleaned.endswith("Z"):
        return datetime.strptime(cleaned, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return datetime.fromisoformat(cleaned).astimezone(UTC)


def _escape_rfc5545_text(text: str) -> str:
    """Escape text characters according to RFC 5545 Section 3.3.11."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _unescape_rfc5545_text(text: str) -> str:
    """Unescape RFC 5545 text sequences."""
    return (
        text.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def serialize_to_ical(events: list[CalendarEventDTO], prodid: str = PRODID) -> str:
    """Serialize a list of CalendarEventDTO objects into an RFC 5545 VCALENDAR string."""
    now_str = _format_rfc5545_datetime(datetime.now(UTC))
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{prodid}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev.external_event_id}")
        lines.append(f"DTSTAMP:{now_str}")
        lines.append(f"DTSTART:{_format_rfc5545_datetime(ev.start_at)}")
        lines.append(f"DTEND:{_format_rfc5545_datetime(ev.end_at)}")
        lines.append(f"SUMMARY:{_escape_rfc5545_text(ev.title)}")
        if ev.description:
            lines.append(f"DESCRIPTION:{_escape_rfc5545_text(ev.description)}")
        if ev.location:
            lines.append(f"LOCATION:{_escape_rfc5545_text(ev.location)}")
        lines.append(f"SEQUENCE:{ev.sequence}")
        lines.append("STATUS:CONFIRMED")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    lines.append("")  # trailing newline
    return "\r\n".join(lines)


def parse_ical_events(ical_content: str) -> list[dict[str, Any]]:
    """Parse an RFC 5545 iCalendar stream into structured event dictionary records."""
    unfolded_lines: list[str] = []
    for line in ical_content.splitlines():
        if not line:
            continue
        if (line.startswith(" ") or line.startswith("\t")) and unfolded_lines:
            unfolded_lines[-1] += line[1:]
        else:
            unfolded_lines.append(line)

    events: list[dict[str, Any]] = []
    current_event: dict[str, Any] | None = None

    for raw_line in unfolded_lines:
        line = raw_line.strip()
        if line == "BEGIN:VEVENT":
            current_event = {}
        elif line == "END:VEVENT":
            if current_event is not None:
                events.append(current_event)
                current_event = None
        elif current_event is not None and ":" in line:
            prop, val = line.split(":", 1)
            # handle parameters e.g. DTSTART;VALUE=DATE:...
            prop_name = prop.split(";")[0].upper()
            if prop_name == "UID":
                current_event["uid"] = val
            elif prop_name == "SUMMARY":
                current_event["summary"] = _unescape_rfc5545_text(val)
            elif prop_name == "DESCRIPTION":
                current_event["description"] = _unescape_rfc5545_text(val)
            elif prop_name == "DTSTART":
                current_event["start_at"] = _parse_rfc5545_datetime(val)
            elif prop_name == "DTEND":
                current_event["end_at"] = _parse_rfc5545_datetime(val)
            elif prop_name == "SEQUENCE":
                try:
                    current_event["sequence"] = int(val)
                except ValueError:
                    current_event["sequence"] = 0

    return events

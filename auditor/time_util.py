"""GTFS HH:MM:SS time parsing and deltas (handles times past midnight)."""

from __future__ import annotations

from datetime import date


def time_to_seconds(t: str) -> int:
    t = str(t).strip()
    parts = t.split(":")
    h = int(parts[0])
    m = int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    return h * 3600 + m * 60 + s


def parse_typed_departure_time(s: str) -> tuple[str | None, str | None]:
    """
    Parse a user-typed departure time for matching GTFS stop_times.
    Accepts HH:MM or HH:MM:SS; hours may exceed 24 (GTFS service day).
    Returns (normalized 'H:MM:SS' string for trip_match, None) or (None, error message).
    """
    raw = str(s).strip()
    if not raw:
        return None, "Enter a first departure time (e.g. 17:32)."

    parts = [p.strip() for p in raw.split(":")]
    if len(parts) == 2:
        h_s, m_s = parts[0], parts[1]
        sec_s = "0"
    elif len(parts) == 3:
        h_s, m_s, sec_s = parts[0], parts[1], parts[2]
    else:
        return None, "Use HH:MM or HH:MM:SS (e.g. 17:32 or 25:30:00)."

    try:
        ih = int(h_s)
        im = int(m_s)
        isec = int(sec_s)
    except ValueError:
        return None, "Time must use numbers only, e.g. 17:32 or 17:32:00."

    if im < 0 or im > 59 or isec < 0 or isec > 59:
        return None, "Minutes and seconds must be between 0 and 59."

    if ih < 0 or ih > 99:
        return None, "Hour must be between 0 and 99 (GTFS allows times past midnight)."

    normalized = f"{ih}:{im:02d}:{isec:02d}"
    return normalized, None


def format_gtfs_time_display(t: str) -> str:
    """Pretty-print GTFS HH:MM:SS (drops :00 seconds; keeps times past midnight as-is)."""
    t = str(t).strip()
    if not t:
        return "—"
    parts = t.split(":")
    if len(parts) >= 3 and parts[2] in ("00", "0"):
        return f"{parts[0]}:{parts[1].zfill(2)}"
    if len(parts) == 2:
        return f"{parts[0]}:{parts[1].zfill(2)}"
    return t


def format_service_date_eu(d: date) -> str:
    """Calendar date as DD/MM/YYYY (EU)."""
    return d.strftime("%d/%m/%Y")


def day_type_label(d: date) -> str:
    """e.g. Monday (weekday) or Saturday (weekend)."""
    name = d.strftime("%A")
    if d.weekday() < 5:
        return f"{name} (weekday)"
    return f"{name} (weekend)"


def format_duration_m_ss(total_seconds: int) -> str:
    """Format a segment length in seconds as M:SS (e.g. 4:30 for 4 min 30 s)."""
    ts = max(0, int(total_seconds))
    m = ts // 60
    s = ts % 60
    return f"{m}:{s:02d}"


def seconds_between(dep_a: str, arr_b: str) -> int:
    """Positive seconds from departure at A to arrival at B on the same trip."""
    da = time_to_seconds(dep_a)
    ab = time_to_seconds(arr_b)
    d = ab - da
    if d < 0:
        d += 86400
    return d

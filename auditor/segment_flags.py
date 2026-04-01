"""Heuristic flags on schedule segments (tight schedule vs shape; trip-relative slowness)."""

from __future__ import annotations

import statistics

from auditor.segments import SegmentRow

# Built-up Ireland: implied speeds above this are worth reviewing (tight schedule vs shape).
HIGH_IMPLIED_SPEED_KMH = 55.0
# Stops that project to the same place on the polyline.
TINY_DISTANCE_M = 1.0

# --- Long-segment low implied speed (not a legal limit; GTFS has no road class) ---
# On legs long enough to be a "through" segment, a very low *average* vs distance often means
# timetable padding, congestion modelled as time, or shape mismatch — worth a look on dual carriageways etc.
# Short hops stay excluded so naturally slow city blocks (e.g. under 500 m between lights) do not flag.
LONG_SEGMENT_MIN_M = 1000.0
LONG_SEGMENT_SLOW_IMPLIED_KMH = 38.0

# --- Trip-relative "slow" ---
# We also flag when this segment is much slower than *other segments on the same trip*,
# and the trip as a whole is not already uniformly slow.
RELATIVE_SLOW_MIN_TRIP_SEGMENTS = 6
RELATIVE_SLOW_MIN_DISTANCE_M = 200.0
# If median implied speed across the trip is below this, we assume the whole service is
# slow urban / congested and skip relative comparison.
RELATIVE_SLOW_MEDIAN_TRIP_FLOOR_KMH = 22.0
# Segment must be below this fraction of the trip median to count as unusually slow.
RELATIVE_SLOW_MEDIAN_RATIO = 0.65


def annotate_segment_flags(rows: list[SegmentRow]) -> None:
    """Populate `row.flags` for each segment. Rules are documented in README / UI caption."""
    for row in rows:
        row.flags.clear()
        if row.time_s <= 0:
            row.flags.append("No schedule time (cannot compute speed)")
            continue
        if row.distance_m < TINY_DISTANCE_M:
            row.flags.append("Tiny shape distance (stops overlap on map?)")
        if row.speed_kmh is None:
            continue
        if row.speed_kmh >= HIGH_IMPLIED_SPEED_KMH:
            row.flags.append(f"Tight schedule (implied ≥{HIGH_IMPLIED_SPEED_KMH:.0f} km/h)")
        if (
            row.distance_m >= LONG_SEGMENT_MIN_M
            and row.speed_kmh < LONG_SEGMENT_SLOW_IMPLIED_KMH
        ):
            row.flags.append(
                f"Slow implied speed on long segment (≥{LONG_SEGMENT_MIN_M:.0f} m, implied <{LONG_SEGMENT_SLOW_IMPLIED_KMH:.0f} km/h)"
            )

    _annotate_slow_vs_trip_median(rows)


def _annotate_slow_vs_trip_median(rows: list[SegmentRow]) -> None:
    """Flag segments much slower than this trip's typical segment (median), when meaningful."""
    pool = [
        r
        for r in rows
        if r.speed_kmh is not None and r.time_s > 0 and r.distance_m >= RELATIVE_SLOW_MIN_DISTANCE_M
    ]
    if len(pool) < RELATIVE_SLOW_MIN_TRIP_SEGMENTS:
        return
    speeds = [r.speed_kmh for r in pool]
    med = statistics.median(speeds)
    if med < RELATIVE_SLOW_MEDIAN_TRIP_FLOOR_KMH:
        return
    cutoff = med * RELATIVE_SLOW_MEDIAN_RATIO
    for row in rows:
        if row.speed_kmh is None or row.time_s <= 0:
            continue
        if row.distance_m < RELATIVE_SLOW_MIN_DISTANCE_M:
            continue
        if row.speed_kmh < cutoff:
            row.flags.append(f"Slower than typical for this trip (median segment ~{med:.0f} km/h)")


def flag_summary(rows: list[SegmentRow]) -> tuple[int, dict[str, int]]:
    """Return (count of rows with any flag, counts by first flag category keyword)."""
    flagged = [r for r in rows if r.flags]
    buckets: dict[str, int] = {}
    for r in flagged:
        for f in r.flags:
            key = f.split("(")[0].strip()
            buckets[key] = buckets.get(key, 0) + 1
    return len(flagged), buckets

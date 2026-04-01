"""Batch scan all trips for a route/day and collect segment-flag summaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from auditor.calendar_util import services_running_on_date
from auditor.segment_flags import annotate_segment_flags
from auditor.segments import build_segment_table
from auditor.trip_match import (
    first_stop_departures,
    load_core_tables,
    load_stop_times_for_trip_ids,
    load_stops,
    shape_for_trip,
)
from auditor.time_util import format_gtfs_time_display


def list_trip_ids_for_route_day(
    gtfs_dir: Path,
    service_date: date,
    route_short_name: str,
    direction_ids: tuple[int, ...] = (0, 1),
) -> tuple[list[str], list[str]]:
    """
    All trip_ids for the route on the calendar day, with shape_id, for given directions.
    Returns (trip_ids sorted, error messages).
    """
    msgs: list[str] = []
    routes, trips, calendar, calendar_dates = load_core_tables(gtfs_dir)
    running = services_running_on_date(calendar, calendar_dates, service_date)
    if not running:
        return [], ["No services running on that date (check calendar in feed)."]

    rname = route_short_name.strip()
    rmatch = routes[routes["route_short_name"].astype(str).str.strip() == rname]
    if rmatch.empty:
        return [], [f'No route with short name "{rname}".']

    route_ids = set(rmatch["route_id"].astype(str))
    dir_set = {str(d) for d in direction_ids}
    td = trips[
        (trips["route_id"].isin(route_ids))
        & (trips["direction_id"].astype(str).isin(dir_set))
        & (trips["service_id"].isin(running))
    ].copy()

    if td.empty:
        return [], ["No trips for that route, direction(s), and service day."]

    td = td[td["shape_id"].notna() & (td["shape_id"].astype(str).str.len() > 0)]
    if td.empty:
        return [], ["Trips found but none have a shape_id (need shapes for distance)."]

    ids = sorted(td["trip_id"].astype(str).unique().tolist())
    return ids, msgs


@dataclass
class TripScanRow:
    trip_id: str
    direction_id: str
    direction_label: str
    first_departure: str
    headsign: str
    segments: int
    flagged_segments: int
    flag_summary: str
    error: str


def scan_trips_for_flags(
    gtfs_dir: Path,
    trip_ids: list[str],
    on_progress: Callable[[int, int], None] | None = None,
) -> list[TripScanRow]:
    """Build segment tables and flags for each trip_id; return one row per trip."""
    if not trip_ids:
        return []

    _, trips, _, _ = load_core_tables(gtfs_dir)
    stops = load_stops(gtfs_dir)
    st_all = load_stop_times_for_trip_ids(gtfs_dir, set(trip_ids))
    first_deps = first_stop_departures(st_all)
    first_map = dict(zip(first_deps["trip_id"].astype(str), first_deps["departure_time"].astype(str)))

    n = len(trip_ids)
    out: list[TripScanRow] = []
    for i, tid in enumerate(trip_ids):
        if on_progress:
            on_progress(i + 1, n)
        tsub = trips[trips["trip_id"].astype(str) == str(tid)]
        if tsub.empty:
            out.append(
                TripScanRow(
                    tid,
                    "",
                    "",
                    "",
                    "",
                    0,
                    0,
                    "",
                    "Trip not in trips.txt",
                )
            )
            continue
        trow = tsub.iloc[0]
        did = str(trow.get("direction_id", "")).strip()
        dlabel = "Outbound" if did == "0" else "Inbound" if did == "1" else f"GTFS {did}"
        hs = str(trow.get("trip_headsign", "") or "").strip()

        st_times = st_all[st_all["trip_id"].astype(str) == str(tid)].copy()
        if st_times.empty:
            out.append(
                TripScanRow(
                    tid,
                    did,
                    dlabel,
                    format_gtfs_time_display(str(first_map.get(tid, ""))),
                    hs,
                    0,
                    0,
                    "",
                    "No stop_times for trip",
                )
            )
            continue

        st_times["stop_sequence"] = st_times["stop_sequence"].astype(int)
        st_times = st_times.sort_values("stop_sequence").reset_index(drop=True)

        shape_df = shape_for_trip(gtfs_dir, trips, tid)
        rows, err = build_segment_table(st_times, stops, shape_df)
        if err:
            dep = format_gtfs_time_display(str(first_map.get(tid, "")))
            out.append(
                TripScanRow(
                    tid,
                    did,
                    dlabel,
                    dep,
                    hs,
                    0,
                    0,
                    "",
                    err,
                )
            )
            continue

        annotate_segment_flags(rows)
        n_seg = len(rows)
        flagged = sum(1 for r in rows if r.flags)
        summaries: list[str] = []
        for r in rows:
            for f in r.flags:
                if f not in summaries:
                    summaries.append(f)
        summary_txt = "; ".join(summaries[:8]) + (" …" if len(summaries) > 8 else "")

        dep = format_gtfs_time_display(str(first_map.get(tid, "")))
        out.append(
            TripScanRow(
                tid,
                did,
                dlabel,
                dep,
                hs,
                n_seg,
                flagged,
                summary_txt,
                "",
            )
        )
    return out


ROUTE_SCAN_EXPORT_COLUMNS = [
    "Trip ID",
    "Direction",
    "First departure",
    "Headsign",
    "Segments",
    "Flagged segments",
    "Flag notes (sample)",
    "Error",
]


def empty_route_scan_export_df() -> pd.DataFrame:
    return pd.DataFrame(columns=ROUTE_SCAN_EXPORT_COLUMNS)


def trip_scan_rows_to_dataframe(rows: list[TripScanRow]) -> pd.DataFrame:
    if not rows:
        return empty_route_scan_export_df()
    return pd.DataFrame(
        [
            {
                "Trip ID": r.trip_id,
                "Direction": r.direction_label,
                "First departure": r.first_departure,
                "Headsign": r.headsign[:60] + ("…" if len(r.headsign) > 60 else ""),
                "Segments": r.segments,
                "Flagged segments": r.flagged_segments,
                "Flag notes (sample)": r.flag_summary,
                "Error": r.error,
            }
            for r in rows
        ]
    )

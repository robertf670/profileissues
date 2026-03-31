"""Resolve trip_id from route, direction, headsign, service day, and terminus departure time."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from auditor.calendar_util import services_running_on_date
from auditor.time_util import time_to_seconds


def load_core_tables(gtfs_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    routes = pd.read_csv(gtfs_dir / "routes.txt", dtype=str, low_memory=False)
    trips = pd.read_csv(gtfs_dir / "trips.txt", dtype=str, low_memory=False)
    cal_path = gtfs_dir / "calendar.txt"
    cd_path = gtfs_dir / "calendar_dates.txt"
    calendar = pd.read_csv(cal_path, dtype=str, low_memory=False) if cal_path.exists() else pd.DataFrame()
    calendar_dates = pd.read_csv(cd_path, dtype=str, low_memory=False) if cd_path.exists() else pd.DataFrame()
    return routes, trips, calendar, calendar_dates


def load_stop_times_for_trip_ids(gtfs_dir: Path, trip_ids: set[str], chunksize: int = 200_000) -> pd.DataFrame:
    path = gtfs_dir / "stop_times.txt"
    if not path.exists():
        return pd.DataFrame()
    chunks = []
    for chunk in pd.read_csv(path, chunksize=chunksize, dtype=str, low_memory=False):
        sub = chunk[chunk["trip_id"].isin(trip_ids)]
        if not sub.empty:
            chunks.append(sub)
    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


def first_stop_departures(stop_times: pd.DataFrame) -> pd.DataFrame:
    """One row per trip_id: departure_time at minimum stop_sequence."""
    if stop_times.empty:
        return pd.DataFrame(columns=["trip_id", "departure_time"])
    st = stop_times.copy()
    st["stop_sequence"] = st["stop_sequence"].astype(int)
    idx = st.groupby("trip_id")["stop_sequence"].idxmin()
    first = st.loc[idx, ["trip_id", "departure_time"]].reset_index(drop=True)
    return first


def match_trip(
    gtfs_dir: Path,
    service_date: date,
    route_short_name: str,
    direction_id: int,
    headsign_contains: str,
    terminus_departure_hhmm: str,
) -> tuple[str | None, list[str], pd.DataFrame]:
    """
    Returns (trip_id or None, messages for UI, candidate_trips DataFrame for debugging).
    """
    msgs: list[str] = []
    routes, trips, calendar, calendar_dates = load_core_tables(gtfs_dir)
    running = services_running_on_date(calendar, calendar_dates, service_date)
    if not running:
        msgs.append("No services running on that date (check calendar in feed).")

    rname = route_short_name.strip()
    rmatch = routes[routes["route_short_name"].astype(str).str.strip() == rname]
    if rmatch.empty:
        return None, [f'No route with short name "{rname}".'], pd.DataFrame()

    route_ids = set(rmatch["route_id"].astype(str))
    td = trips[
        (trips["route_id"].isin(route_ids))
        & (trips["direction_id"].astype(str) == str(direction_id))
        & (trips["service_id"].isin(running))
    ].copy()

    if td.empty:
        return None, ["No trips for that route/direction/service day."], pd.DataFrame()

    td = td[td["shape_id"].notna() & (td["shape_id"].astype(str).str.len() > 0)]
    if td.empty:
        return None, ["Trips found but none have a shape_id (need shapes for distance)."], pd.DataFrame()

    if headsign_contains.strip():
        needle = headsign_contains.strip().lower()
        hs = td["trip_headsign"].fillna("").str.lower()
        td = td[hs.str.contains(needle, regex=False, na=False)]
    if td.empty:
        return None, [f'No trips with headsign containing "{headsign_contains}".'], pd.DataFrame()

    cand_ids = set(td["trip_id"].astype(str))

    st = load_stop_times_for_trip_ids(gtfs_dir, cand_ids)
    if st.empty:
        return None, ["Could not load stop_times for candidate trips."], td

    first_deps = first_stop_departures(st)
    first_deps["dep_sec"] = first_deps["departure_time"].map(time_to_seconds)

    user_parts = terminus_departure_hhmm.strip().split(":")
    uh = int(user_parts[0])
    um = int(user_parts[1]) if len(user_parts) > 1 else 0
    us = int(user_parts[2]) if len(user_parts) > 2 else 0
    user_sec = uh * 3600 + um * 60 + us

    merged = first_deps[first_deps["dep_sec"] == user_sec]
    trip_ids = merged["trip_id"].astype(str).tolist()
    if len(trip_ids) == 0:
        return None, msgs + ["No trip with that first departure time at the terminus."], td
    if len(trip_ids) > 1:
        sub = td[td["trip_id"].astype(str).isin(trip_ids)]
        heads = sorted(
            {str(h).strip() for h in sub["trip_headsign"].fillna("").unique() if str(h).strip()}
        )
        head_note = ""
        if heads:
            head_note = " Headsigns in GTFS for these trips: " + "; ".join(heads[:15]) + (
                " …" if len(heads) > 15 else ""
            )
        return (
            None,
            msgs
            + [
                f"Multiple trips match ({len(trip_ids)}): {', '.join(trip_ids[:20])}"
                + (" …" if len(trip_ids) > 20 else "")
                + ". Add a headsign substring that appears only on your trip, or try another day."
                + head_note
            ],
            td,
        )
    tid = trip_ids[0]
    return tid, msgs, td[td["trip_id"].astype(str) == tid]


def stop_times_for_trip(gtfs_dir: Path, trip_id: str) -> pd.DataFrame:
    """Load only rows for one trip_id (chunked scan)."""
    return load_stop_times_for_trip_ids(gtfs_dir, {trip_id})


def shape_for_trip(gtfs_dir: Path, trips_table: pd.DataFrame, trip_id: str) -> pd.DataFrame:
    sub = trips_table[trips_table["trip_id"].astype(str) == str(trip_id)]
    if sub.empty:
        return pd.DataFrame()
    sid = str(sub.iloc[0]["shape_id"])
    shapes = pd.read_csv(gtfs_dir / "shapes.txt", dtype=str, low_memory=False)
    return shapes[shapes["shape_id"].astype(str) == sid]


def load_stops(gtfs_dir: Path) -> pd.DataFrame:
    return pd.read_csv(gtfs_dir / "stops.txt", dtype=str, low_memory=False)

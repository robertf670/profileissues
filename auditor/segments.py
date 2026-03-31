"""Shape-based segment distances and scheduled speeds."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import substring

from auditor.time_util import seconds_between


def _stop_time_str(row: pd.Series, prefer: tuple[str, ...]) -> str:
    for k in prefer:
        v = row.get(k)
        if pd.isna(v):
            continue
        s = str(v).strip()
        if s and s.lower() != "nan":
            return s
    return ""

# TM75 / Irish Grid — distances in meters for projection.
_TRANSFORMER = Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)


def _line_from_shape(shape_df: pd.DataFrame) -> LineString:
    sdf = shape_df.copy()
    sdf["shape_pt_sequence"] = sdf["shape_pt_sequence"].astype(int)
    sdf = sdf.sort_values("shape_pt_sequence")
    xs, ys = [], []
    for _, row in sdf.iterrows():
        lon, lat = float(row["shape_pt_lon"]), float(row["shape_pt_lat"])
        x, y = _TRANSFORMER.transform(lon, lat)
        xs.append(x)
        ys.append(y)
    return LineString(list(zip(xs, ys)))


def _project_m(line: LineString, lon: float, lat: float) -> float:
    x, y = _TRANSFORMER.transform(lon, lat)
    p = Point(x, y)
    return line.project(p)


def _distances_along_line(line: LineString, st: pd.DataFrame, stop_index: pd.DataFrame) -> list[float]:
    out: list[float] = []
    for _, row in st.iterrows():
        sid = str(row["stop_id"])
        srow = stop_index.loc[sid]
        if isinstance(srow, pd.DataFrame):
            srow = srow.iloc[0]
        lon, lat = float(srow["stop_lon"]), float(srow["stop_lat"])
        out.append(_project_m(line, lon, lat))
    return out


def _orient_line_with_stops(line: LineString, st: pd.DataFrame, stop_index: pd.DataFrame) -> tuple[LineString, list[float]]:
    """
    GTFS shapes may be digitised opposite to the trip direction. Orient so that,
    in trip order, projections along the line are mostly increasing from first to last stop.
    """
    dist = _distances_along_line(line, st, stop_index)
    if len(dist) >= 2 and dist[-1] < dist[0]:
        rev = LineString(list(line.coords)[::-1])
        dist = _distances_along_line(rev, st, stop_index)
        return rev, dist
    return line, dist


@dataclass
class SegmentRow:
    from_stop_name: str
    to_stop_name: str
    from_stop_id: str
    to_stop_id: str
    depart_from_scheduled: str
    arrive_to_scheduled: str
    distance_m: float
    time_s: int
    speed_kmh: float | None


def build_segment_table(
    stop_times_trip: pd.DataFrame,
    stops: pd.DataFrame,
    shape_df: pd.DataFrame,
) -> tuple[list[SegmentRow], str | None]:
    """
    stop_times_trip: rows for one trip_id, sorted by stop_sequence.
    shape_df: rows for the trip's shape_id.
    """
    st = stop_times_trip.copy()
    st["stop_sequence"] = st["stop_sequence"].astype(int)
    st = st.sort_values("stop_sequence").reset_index(drop=True)
    if st.empty:
        return [], "No stop times for trip."

    stop_index = stops.set_index("stop_id")
    for sid in st["stop_id"].astype(str):
        if sid not in stop_index.index:
            return [], f"Missing stop_id in stops.txt: {sid}"

    line = _line_from_shape(shape_df)
    if line.length == 0 or math.isnan(line.length):
        return [], "Invalid or empty shape geometry."

    line, dist_along = _orient_line_with_stops(line, st, stop_index)

    rows: list[SegmentRow] = []
    for i in range(len(st) - 1):
        dep = str(st.iloc[i]["departure_time"])
        arr = str(st.iloc[i + 1]["arrival_time"])
        time_s = seconds_between(dep, arr)
        a, b = dist_along[i], dist_along[i + 1]
        lo, hi = min(a, b), max(a, b)
        d_m = substring(line, lo, hi).length
        if time_s <= 0:
            speed = None
        else:
            speed = (d_m / 1000.0) / (time_s / 3600.0)

        sid_a = str(st.iloc[i]["stop_id"])
        sid_b = str(st.iloc[i + 1]["stop_id"])
        ra = stop_index.loc[sid_a]
        rb = stop_index.loc[sid_b]
        if isinstance(ra, pd.DataFrame):
            ra = ra.iloc[0]
        if isinstance(rb, pd.DataFrame):
            rb = rb.iloc[0]
        name_a = str(ra.get("stop_name", sid_a))
        name_b = str(rb.get("stop_name", sid_b))

        dep_from = _stop_time_str(st.iloc[i], ("departure_time", "arrival_time"))
        arr_to = _stop_time_str(st.iloc[i + 1], ("arrival_time", "departure_time"))
        rows.append(
            SegmentRow(
                from_stop_name=name_a,
                to_stop_name=name_b,
                from_stop_id=sid_a,
                to_stop_id=sid_b,
                depart_from_scheduled=dep_from,
                arrive_to_scheduled=arr_to,
                distance_m=d_m,
                time_s=time_s,
                speed_kmh=speed,
            )
        )

    return rows, None

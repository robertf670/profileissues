"""Folium map: GTFS shape polyline + trip stops."""

from __future__ import annotations

import math

import folium
import pandas as pd


def build_route_map(
    shape_df: pd.DataFrame,
    stop_times_trip: pd.DataFrame,
    stops: pd.DataFrame,
) -> folium.Map | None:
    """
    Interactive map: blue polyline = published shape, numbered markers = stops in trip order.
    Returns None if there is no shape data.
    """
    if shape_df.empty:
        return None

    sdf = shape_df.copy()
    sdf["shape_pt_sequence"] = sdf["shape_pt_sequence"].astype(int)
    sdf = sdf.sort_values("shape_pt_sequence")
    line_points: list[tuple[float, float]] = []
    for _, row in sdf.iterrows():
        lat, lon = float(row["shape_pt_lat"]), float(row["shape_pt_lon"])
        if math.isfinite(lat) and math.isfinite(lon):
            line_points.append((lat, lon))

    if len(line_points) < 2:
        return None

    stt = stop_times_trip.copy()
    stt["stop_sequence"] = stt["stop_sequence"].astype(int)
    stt = stt.sort_values("stop_sequence").reset_index(drop=True)

    stop_index = stops.set_index("stop_id")
    stop_markers: list[tuple[float, float, str, int]] = []
    for _, row in stt.iterrows():
        sid = str(row["stop_id"])
        if sid not in stop_index.index:
            continue
        srow = stop_index.loc[sid]
        if isinstance(srow, pd.DataFrame):
            srow = srow.iloc[0]
        lat, lon = float(srow["stop_lat"]), float(srow["stop_lon"])
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        name = str(srow.get("stop_name", sid))
        seq = int(row["stop_sequence"])
        stop_markers.append((lat, lon, name, seq))

    all_lats = [p[0] for p in line_points] + [m[0] for m in stop_markers]
    all_lons = [p[1] for p in line_points] + [m[1] for m in stop_markers]
    mid_lat = sum(all_lats) / len(all_lats)
    mid_lon = sum(all_lons) / len(all_lons)

    m = folium.Map(location=[mid_lat, mid_lon], zoom_start=12, tiles="OpenStreetMap")

    folium.PolyLine(
        locations=line_points,
        color="#1d4ed8",
        weight=4,
        opacity=0.85,
        tooltip="GTFS route shape",
    ).add_to(m)

    for lat, lon, name, seq in stop_markers:
        folium.CircleMarker(
            location=[lat, lon],
            radius=5,
            color="#b91c1c",
            weight=2,
            fill=True,
            fill_color="#fecaca",
            fill_opacity=0.9,
            tooltip=f"{seq}. {name}",
        ).add_to(m)

    sw_lat, ne_lat = min(all_lats), max(all_lats)
    sw_lon, ne_lon = min(all_lons), max(all_lons)
    pad = 0.005
    m.fit_bounds([[sw_lat - pad, sw_lon - pad], [ne_lat + pad, ne_lon + pad]])

    return m

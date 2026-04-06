"""Streamlit: Dublin Bus scheduled segment speeds (GTFS)."""

from __future__ import annotations

import csv
import os
import uuid
import zipfile
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from io import BytesIO, StringIO
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from streamlit_folium import st_folium

from auditor.download import (
    DEFAULT_URL,
    check_feed_update_available,
    data_dir,
    download_gtfs,
    read_last_download_utc,
)
from auditor.route_scan import (
    TripScanRow,
    empty_route_scan_export_df,
    list_trip_ids_for_route_day,
    scan_trips_for_flags,
    trip_scan_rows_to_dataframe,
)
from auditor.segment_flags import annotate_segment_flags, flag_summary
from auditor.segments import build_segment_table
from auditor.excel_export import TripMeta, build_audit_excel_bytes, build_route_scan_excel_bytes
from auditor.route_map import build_route_map
from auditor.time_util import (
    day_type_label,
    format_duration_m_ss,
    format_gtfs_time_display,
    format_service_date_eu,
    parse_typed_departure_time,
    time_to_filename_hhmm,
    time_to_seconds,
)
from auditor.trip_match import (
    first_stop_departures,
    load_core_tables,
    load_stops,
    match_trip,
    shape_for_trip,
    stop_times_for_trip,
)
from auditor.url_state import hydrate_from_url_once, init_audit_widget_defaults, sync_audit_to_url

load_dotenv()

ROOT = Path(__file__).resolve().parent

_MAX_AUDIT_EXTRA_LEGS = 50


def _audit_clear_extra_leg_session_keys(leg_id: str) -> None:
    """Drop widget session keys for one extra leg slot (after Remove leg)."""
    for _s in ("service_date", "route", "direction", "departure_time"):
        st.session_state.pop(f"audit_extra_{leg_id}_{_s}", None)


def _as_service_date(val: object, fallback: date) -> date:
    """Normalize date_input / session values to ``date``."""
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return fallback


def _collect_audit_legs(
    primary_service_date: date,
    primary_route: str,
    primary_direction: tuple[object, ...],
    primary_dep_raw: str,
    extra_leg_ids: list[str],
) -> list[tuple[int, date, str, int, str]]:
    """Build (1-based leg index, service date, route, direction_id, departure raw) for primary + extras."""
    legs: list[tuple[int, date, str, int, str]] = []
    d0 = int(primary_direction[1])
    legs.append(
        (1, primary_service_date, str(primary_route).strip(), d0, str(primary_dep_raw))
    )
    for i, leg_id in enumerate(extra_leg_ids):
        sd = st.session_state.get(f"audit_extra_{leg_id}_service_date")
        r = st.session_state.get(f"audit_extra_{leg_id}_route", "")
        dir_t = st.session_state.get(f"audit_extra_{leg_id}_direction")
        dep = st.session_state.get(f"audit_extra_{leg_id}_departure_time", "")
        if dir_t is None:
            dir_t = ("Outbound (GTFS 0)", 0)
        d_id = int(dir_t[1]) if isinstance(dir_t, tuple) else int(dir_t)
        sd_eff = _as_service_date(sd, primary_service_date)
        legs.append((2 + i, sd_eff, str(r).strip(), d_id, str(dep)))
    return legs


# Streamlit's HTML table ignores pandas Styler alignment; CSS centers cells.
# Use a display-only frame with formatted strings so floats don't show as 392.500000.
_AUDIT_TABLE_CSS = """
<style>
/* Bounded HTML tables (segment audit + route scan summary): not Glide canvas; scroll inside box. */
.bounded-html-table-wrap {
    overflow: auto;
    -webkit-overflow-scrolling: touch;
    width: 100%;
    max-height: min(65vh, 28rem);
    border-radius: 0.35rem;
    border: 1px solid rgba(128, 128, 128, 0.28);
}
.bounded-html-table {
    width: max-content;
    border-collapse: collapse;
}
.bounded-html-table th,
.bounded-html-table td {
    text-align: center;
    vertical-align: middle;
    padding: 0.35rem 0.75rem;
    border-bottom: 1px solid rgba(128, 128, 128, 0.28);
}
.bounded-html-table th {
    font-weight: 600;
    white-space: nowrap;
}
.bounded-html-table.segment-audit th:last-child,
.bounded-html-table.segment-audit td:last-child {
    text-align: left;
    white-space: normal;
    word-break: break-word;
    min-width: 16rem;
}
/* Flag notes + Error (columns 7–8 in trip_scan_rows_to_dataframe order). */
.bounded-html-table.route-scan-results th:nth-child(7),
.bounded-html-table.route-scan-results td:nth-child(7),
.bounded-html-table.route-scan-results th:nth-child(8),
.bounded-html-table.route-scan-results td:nth-child(8) {
    text-align: left;
    white-space: normal;
    word-break: break-word;
    min-width: 10rem;
}
</style>
"""

_AUDIT_FLAGS_EXPANDER_MD = """
Flags are **hints**, not proof of an error. They compare timetable time to **distance along the GTFS shape**.

| Flag | Meaning |
|------|---------|
| **No schedule time** | Arrival at B is not after departure at A in the feed (or zero seconds). Speed is not computed. |
| **Tiny shape distance** | The two stops project to the same place on the polyline (under about **1 m**). Check duplicate stops or shape alignment. |
| **Tight schedule** | Implied **average** speed is **≥ 55 km/h** — timetable is tight vs the mapped distance. |
| **Slow implied speed on long segment** | Shape distance **≥ about 1 km** but implied average **under ~38 km/h**. Flags **through** legs where the timetable allows a very low average over distance — **not** a speed-limit check (GTFS has no road class). Short segments are excluded so small congested hops are not flagged. |
| **Slower than typical for this trip** | When the trip has enough segments: this leg is **well below** the trip **median** implied speed (and the trip is not already uniformly slow). |

Thresholds are in `auditor/segment_flags.py` (`LONG_SEGMENT_*`, trip-relative constants).
"""


def _audit_table_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Formatted for the on-screen table only; exports keep numeric `df`."""
    out = df.copy()
    out["Distance along shape (m)"] = out["Distance along shape (m)"].map(
        lambda x: f"{float(x):.1f}" if pd.notna(x) else ""
    )
    out["Scheduled time (s)"] = out["Scheduled time (s)"].map(
        lambda x: str(int(x)) if pd.notna(x) else ""
    )
    out["Implied speed (km/h)"] = out["Implied speed (km/h)"].map(
        lambda x: f"{float(x):.2f}" if pd.notna(x) else ""
    )
    if "Flag(s)" in out.columns:
        out["Flag(s)"] = out["Flag(s)"].astype(str)
    return out


def _render_segment_audit_table(df: pd.DataFrame) -> None:
    """Show segment rows as HTML (not ``st.dataframe``): Streamlit uses a canvas grid that clips long cells."""
    disp = _audit_table_for_display(df)
    html_table = disp.to_html(index=False, classes="bounded-html-table segment-audit", escape=True)
    st.markdown(
        f'<div class="bounded-html-table-wrap">{html_table}</div>',
        unsafe_allow_html=True,
    )


def _render_route_scan_results_table(df: pd.DataFrame) -> None:
    """Same bounded HTML table as segment audit; summary has long flag notes / errors."""
    html_table = df.to_html(index=False, classes="bounded-html-table route-scan-results", escape=True)
    st.markdown(
        f'<div class="bounded-html-table-wrap">{html_table}</div>',
        unsafe_allow_html=True,
    )


def _export_download_row() -> None:
    """Margin above export buttons (Excel and CSV are stacked below)."""
    st.markdown('<div style="margin-top: 1.5rem;"></div>', unsafe_allow_html=True)


def _env(key: str, default: str | None = None) -> str | None:
    """Local `.env` / process env, then Streamlit Community Cloud **Secrets** (`st.secrets`)."""
    import os

    v = os.getenv(key)
    if v is not None and str(v).strip() != "":
        return v.strip()
    try:
        if key in st.secrets:
            sv = st.secrets[key]
            if sv is not None and str(sv).strip() != "":
                return str(sv).strip()
    except Exception:
        pass
    return default


def _format_feed_calendar_cell(raw: object) -> str:
    """GTFS feed_info dates are often YYYYMMDD; show as DD/MM/YYYY."""
    s = str(raw).strip() if raw is not None and pd.notna(raw) else ""
    if len(s) == 8 and s.isdigit():
        y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
        try:
            return format_service_date_eu(date(y, m, d))
        except ValueError:
            return s
    return s if s else "—"


def _short_feed_version(v: str) -> str:
    v = v.strip()
    if len(v) <= 20:
        return v
    return f"{v[:8]}…{v[-4:]}"


def _md_safe(s: str) -> str:
    """Escape characters that would break Markdown emphasis/code in feed strings."""
    return s.replace("\\", "\\\\").replace("`", "\\`").replace("*", "\\*")


def _format_last_download_dublin(dt: datetime) -> str:
    """UTC or aware datetime → DD/MM/YYYY HH:MM:SS in Europe/Dublin."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Europe/Dublin")).strftime("%d/%m/%Y %H:%M:%S")


def _route_scan_meta_strings(rs: dict) -> dict[str, str]:
    """Strings for route-scan CSV/Excel headers."""
    scanned = str(rs["total"])
    if rs.get("truncated"):
        scanned = f"{rs['total']} (of {rs['total_in_feed']} in feed; capped at 500)"
    return {
        "route": str(rs["route"]),
        "service_date": format_service_date_eu(rs["date"]),
        "directions": str(rs["directions"]),
        "trips_scanned": scanned,
        "with_flags": str(rs["with_flags"]),
        "with_errors": str(rs["with_errors"]),
    }


def _route_scan_csv_text(rs: dict) -> str:
    buf = StringIO()
    cw = csv.writer(buf)
    m = _route_scan_meta_strings(rs)
    cw.writerow(["Route", m["route"]])
    cw.writerow(["Service date", m["service_date"]])
    cw.writerow(["Directions", m["directions"]])
    cw.writerow(["Trips scanned", m["trips_scanned"]])
    cw.writerow(["Trips with ≥1 flagged segment", m["with_flags"]])
    cw.writerow(["Trips with build errors", m["with_errors"]])
    cw.writerow([])
    df = rs.get("df")
    if df is not None and not df.empty:
        df.to_csv(buf, index=False)
    else:
        cw.writerow(["(No problem trips — table empty.)"])
    return buf.getvalue()


def _safe_filename_part(s: str, max_len: int = 64) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(s))
    return out[:max_len] if len(out) > max_len else out


def _filename_ddmmyyyy(d: date) -> str:
    return d.strftime("%d%m%Y")


def _filename_in_out_from_direction_id(direction_id: int) -> str:
    """GTFS 0 → OUT (outbound), 1 → IN (inbound); other values stay explicit."""
    if direction_id == 0:
        return "OUT"
    if direction_id == 1:
        return "IN"
    return f"D{direction_id}"


def _audit_schedule_export_base_name(
    route: str, hhmm: str, leg_sd: date, direction_id: int
) -> str:
    """Same stem as single-trip Excel/CSV: ``{route}_{HHMM}_{DDMMYYYY}_{OUT|IN}_Audit``."""
    _hhmm_fn = time_to_filename_hhmm(hhmm)
    return (
        f"{route.strip()}_{_hhmm_fn}_{_filename_ddmmyyyy(leg_sd)}_"
        f"{_filename_in_out_from_direction_id(int(direction_id))}_Audit"
    )


def _filename_in_out_from_route_scan_dirs(dirs: tuple) -> str:
    """Route scan may use one or both directions."""
    s = sorted({int(x) for x in dirs})
    if s == [0, 1]:
        return "IN_OUT"
    if s == [0]:
        return "OUT"
    if s == [1]:
        return "IN"
    return "_".join(str(x) for x in s)


def _route_scan_trip_sort_key(r: TripScanRow) -> tuple:
    # getattr: session_state may hold TripScanRow instances from before first_dep_raw existed
    raw = (getattr(r, "first_dep_raw", None) or "").strip()
    try:
        sec = time_to_seconds(raw) if raw and ":" in raw else 999_999
    except (ValueError, TypeError):
        sec = 999_999
    try:
        di = int(r.direction_id) if r.direction_id.isdigit() else 99
    except ValueError:
        di = 99
    return (di, sec, r.trip_id)


def _route_scan_trip_choice_label(r: TripScanRow) -> str:
    dep = r.first_departure if r.first_departure and r.first_departure != "—" else "?"
    d = r.direction_label or "?"
    hs = (r.headsign or "").strip()
    hs_short = hs[:50] + ("…" if len(hs) > 50 else "") if hs else ""
    tid = r.trip_id
    parts = [dep, d]
    if hs_short:
        parts.append(hs_short)
    return " · ".join(parts) + f" ({tid})"


def _render_single_trip_drilldown(
    gtfs_dir: Path,
    trip_id: str,
    service_date: date,
    route_short: str,
) -> None:
    """Full segment table, map, flags, downloads for one trip_id (used from route scan only)."""
    _, trips, _, _ = load_core_tables(gtfs_dir)
    st_times = stop_times_for_trip(gtfs_dir, trip_id)
    if st_times.empty:
        st.error("No stop times for this trip.")
        return
    shape_df = shape_for_trip(gtfs_dir, trips, trip_id)
    stops = load_stops(gtfs_dir)
    rows, err = build_segment_table(st_times, stops, shape_df)
    if err:
        st.error(err)
        return
    annotate_segment_flags(rows)

    tmatch = trips[trips["trip_id"].astype(str) == str(trip_id)]
    if tmatch.empty:
        st.error("Trip not found in trips.txt.")
        return
    did = int(float(str(tmatch.iloc[0].get("direction_id", 0))))
    direction_label = "Outbound" if did == 0 else "Inbound" if did == 1 else f"GTFS {did}"
    fd = first_stop_departures(st_times)
    dep_raw = str(fd.iloc[0]["departure_time"])
    terminus_dep_display = format_gtfs_time_display(dep_raw)
    trip_meta: TripMeta = {
        "route": route_short.strip(),
        "direction": direction_label,
        "terminus_departure": terminus_dep_display,
        "service_date_display": format_service_date_eu(service_date),
        "day_type": day_type_label(service_date),
    }

    st.markdown(f"### Trip **{trip_id}**")

    folium_map = build_route_map(shape_df, st_times, stops)
    if folium_map is not None:
        st.caption(
            "Blue line: GTFS **shape**. Red dots: **stops** on this trip. "
            "Map data © OpenStreetMap contributors."
        )
        st_folium(
            folium_map,
            use_container_width=True,
            height=480,
            returned_objects=[],
            key=f"route_scan_map_{_safe_filename_part(trip_id)}",
        )

    st.markdown(
        f"**Trip** · Route **{trip_meta['route']}** · **{trip_meta['direction']}** · "
        f"Terminus departure **{trip_meta['terminus_departure']}**"
    )

    n_flagged, flag_buckets = flag_summary(rows)
    if n_flagged:
        parts = [f"{k}: {v}" for k, v in sorted(flag_buckets.items(), key=lambda x: (-x[1], x[0]))]
        st.warning("**" + str(n_flagged) + "** segment(s) flagged — " + " · ".join(parts))
    else:
        st.caption("No heuristic flags on this trip (defaults in `auditor/segment_flags.py`).")

    df = pd.DataFrame(
        {
            "From stop": [r.from_stop_name for r in rows],
            "To stop": [r.to_stop_name for r in rows],
            "Timetable depart (from)": [format_gtfs_time_display(r.depart_from_scheduled) for r in rows],
            "Timetable arrive (to)": [format_gtfs_time_display(r.arrive_to_scheduled) for r in rows],
            "Distance along shape (m)": [round(r.distance_m, 1) for r in rows],
            "Scheduled time (s)": [r.time_s for r in rows],
            "Scheduled time (M:SS)": [format_duration_m_ss(r.time_s) for r in rows],
            "Implied speed (km/h)": [round(r.speed_kmh, 2) if r.speed_kmh is not None else None for r in rows],
            "Flag(s)": ["; ".join(r.flags) if r.flags else "—" for r in rows],
        }
    )

    _render_segment_audit_table(df)

    _sn = _safe_filename_part(route_short.strip())
    _tid = _safe_filename_part(trip_id)
    base_name = (
        f"RouteScan_{_sn}_{_filename_ddmmyyyy(service_date)}_{_filename_in_out_from_direction_id(did)}_trip_{_tid}"
    )

    csv_buf = StringIO()
    cw = csv.writer(csv_buf)
    cw.writerow(["Trip ID", trip_id])
    cw.writerow(["Route number", trip_meta["route"]])
    cw.writerow(["Direction", trip_meta["direction"]])
    cw.writerow(["Terminus departure (scheduled)", trip_meta["terminus_departure"]])
    cw.writerow(["Service date", trip_meta["service_date_display"]])
    cw.writerow(["Day type", trip_meta["day_type"]])
    cw.writerow([])
    df.to_csv(csv_buf, index=False)
    xlsx_bytes = build_audit_excel_bytes(df, trip_meta)

    _export_download_row()
    st.download_button(
        "Download this trip (Excel)",
        data=xlsx_bytes,
        file_name=f"{base_name}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"route_scan_detail_xlsx_{_tid}",
    )
    st.download_button(
        "Download this trip (CSV)",
        data=csv_buf.getvalue(),
        file_name=f"{base_name}.csv",
        mime="text/csv",
        key=f"route_scan_detail_csv_{_tid}",
    )


def _route_scan_sidebar_visible() -> bool:
    """Batch route scan: on everywhere unless opted out via HIDE_ROUTE_SCAN=1 (env or Streamlit secrets)."""
    return os.getenv("HIDE_ROUTE_SCAN", "").lower() not in ("1", "true", "yes")


def _resolve_last_download_time(gtfs_dir: Path) -> datetime | None:
    """Prefer on-disk marker from download; else session; else trips.txt mtime (legacy extracts)."""
    recorded = read_last_download_utc(ROOT)
    if recorded is not None:
        return recorded
    if "gtfs_downloaded_at" in st.session_state:
        try:
            raw = st.session_state["gtfs_downloaded_at"]
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            pass
    trips = gtfs_dir / "trips.txt"
    if trips.exists():
        try:
            return datetime.fromtimestamp(trips.stat().st_mtime, tz=timezone.utc)
        except OSError:
            pass
    return None


def _render_feed_info(gtfs_dir: Path) -> None:
    """Show feed_info.txt as a compact labelled block (not raw column names)."""
    p = gtfs_dir / "feed_info.txt"
    if not p.exists():
        st.caption("No feed_info.txt in this extract.")
        return
    try:
        df = pd.read_csv(p, dtype=str)
        if df.empty:
            st.caption("feed_info.txt is empty.")
            return
        row = df.iloc[0]
        pub = str(row.get("feed_publisher_name", "") or "").strip() or "—"
        ver = str(row.get("feed_version", "") or "").strip()
        d1 = _format_feed_calendar_cell(row.get("feed_start_date"))
        d2 = _format_feed_calendar_cell(row.get("feed_end_date"))
        ver_disp = _short_feed_version(ver) if ver else "—"
        ps, d1s, d2s, vs = (_md_safe(x) for x in (pub, d1, d2, ver_disp))
        # Native bordered container follows Streamlit light/dark theme (no fixed light-gray panel).
        with st.container(border=True):
            st.markdown(
                f"**GTFS feed** · {ps}  \n"
                f"**Valid in feed** {d1s} – {d2s}  \n"
                f"**Version** `{vs}`",
            )
    except Exception as exc:  # noqa: BLE001
        st.caption(f"Could not read feed_info: {exc}")


st.set_page_config(page_title="Dublin Bus schedule auditor", layout="wide")
st.markdown(_AUDIT_TABLE_CSS, unsafe_allow_html=True)
st.title("Dublin Bus schedule auditor")
st.caption(
    "Compares the **published timetable** to **distance along the GTFS shape** between consecutive stops."
)
st.info(
    "**What this is:** for each stop-to-stop segment, **implied speed** is a single **average** — shape "
    "distance divided by **scheduled** time from departure at one stop to arrival at the next. That is what "
    "the timetable **allows** along the published polyline, not how fast a bus “should” drive.\n\n"
    "**What this is not:** a traffic or road model. GTFS has no **posted speed limits**, signals, junction "
    "delays, or road class — so a **low average** (e.g. ~20 km/h) is **not** automatically wrong when the "
    "street limit is 50–60 km/h; frequent stops and slow **averages** between timing points are normal.\n\n"
    "**Operations:** the app does **not** know **skip-to-serve**, boarding time, or **AVL / GTFS-RT** — only "
    "static schedule and shape."
)

gtfs_dir = data_dir(ROOT)
dl_url = _env("GTFS_DOWNLOAD_URL", DEFAULT_URL)
api_key = _env("NTA_API_KEY")

with st.sidebar:
    st.subheader("GTFS data")
    st.text(f"Folder: {gtfs_dir}")
    _last_dl = _resolve_last_download_time(gtfs_dir)
    if _last_dl is not None:
        st.caption(
            f"Last downloaded: **{_format_last_download_dublin(_last_dl)}** (Europe/Dublin)"
        )
    else:
        st.caption("Last downloaded: — (not yet on this machine)")
    if st.button(
        "Check for update",
        key="gtfs_check_update",
        help="HEAD request against the feed URL: compares ETag / Last-Modified to your last download. Does not download data.",
    ):
        with st.spinner("Checking remote feed…"):
            st.session_state["gtfs_check_result"] = check_feed_update_available(dl_url, api_key, ROOT)
    if "gtfs_check_result" in st.session_state:
        _cr = st.session_state["gtfs_check_result"]
        if _cr.kind == "newer":
            st.warning(_cr.message)
        elif _cr.kind == "current":
            st.success(_cr.message)
        elif _cr.kind == "error":
            st.error(_cr.message)
        else:
            st.info(_cr.message)
    if st.button("Download / refresh GTFS"):
        with st.spinner("Downloading and extracting…"):
            try:
                _, at = download_gtfs(dl_url, api_key, ROOT)
                st.session_state["gtfs_downloaded_at"] = at.isoformat()
                st.session_state.pop("gtfs_check_result", None)
                st.success("Done.")
            except Exception as e:  # noqa: BLE001
                st.error(str(e))

ready = (gtfs_dir / "trips.txt").exists()
if not ready:
    st.warning("Download GTFS using the sidebar before running an audit.")
    st.stop()

with st.sidebar:
    if _route_scan_sidebar_visible():
        st.divider()
        st.subheader("Route scan")
        st.caption(
            "Every trip on one route for a day; lists trips with segment flags or errors. "
            "Can be CPU-heavy on large routes (max 500 trips). Set `HIDE_ROUTE_SCAN=1` in "
            "`.env` or Streamlit secrets to hide this block."
        )
        _scan_route_in = st.text_input(
            "Route number",
            key="scan_route_short",
            placeholder="e.g. 39",
            help="Same as main form: routes.route_short_name.",
        )
        _scan_day = st.date_input(
            "Service date",
            format="DD/MM/YYYY",
            key="scan_route_date",
        )
        _scan_dirs = st.multiselect(
            "Directions",
            options=[0, 1],
            default=[0, 1],
            format_func=lambda x: "Outbound (GTFS 0)" if x == 0 else "Inbound (GTFS 1)",
            help="Default: both. Narrow to one direction if you prefer.",
        )
        if st.button("Scan route for flags", key="scan_route_btn", type="secondary"):
            st.session_state.pop("route_scan_drill_trip_id", None)
            _rname = str(_scan_route_in or "").strip()
            if not _rname:
                st.session_state["_route_scan_result"] = {"error": "Enter a route number."}
            else:
                _dirs = tuple(_scan_dirs) if _scan_dirs else (0, 1)
                _ids, _msgs = list_trip_ids_for_route_day(gtfs_dir, _scan_day, _rname, _dirs)
                if not _ids:
                    st.session_state["_route_scan_result"] = {
                        "error": "; ".join(_msgs) if _msgs else "No trips found.",
                        "messages": _msgs,
                    }
                else:
                    _cap = 500
                    _trunc = len(_ids) > _cap
                    _id_list = _ids[:_cap]
                    _prog = st.progress(0)

                    def _cb(cur: int, total: int) -> None:
                        _prog.progress(min(1.0, cur / total))

                    try:
                        _rows = scan_trips_for_flags(gtfs_dir, _id_list, on_progress=_cb)
                    finally:
                        _prog.empty()
                    _problem = [r for r in _rows if r.flagged_segments > 0 or r.error]
                    st.session_state["_route_scan_result"] = {
                        "route": _rname,
                        "date": _scan_day,
                        "directions": _dirs,
                        "total": len(_rows),
                        "total_in_feed": len(_ids),
                        "truncated": _trunc,
                        "with_flags": sum(1 for r in _rows if r.flagged_segments > 0),
                        "with_errors": sum(1 for r in _rows if r.error),
                        "df": trip_scan_rows_to_dataframe(_problem),
                        "messages": _msgs,
                        "all_trip_ids": [str(x) for x in _id_list],
                        "scan_rows": _rows,
                    }

_render_feed_info(gtfs_dir)

_rs = st.session_state.get("_route_scan_result")
if _rs is not None:
    with st.expander("Route scan results", expanded=True):
        if _rs.get("error"):
            st.warning(_rs["error"])
        else:
            st.markdown(
                f"**Route {_rs['route']}** · **{format_service_date_eu(_rs['date'])}** · "
                f"directions `{_rs['directions']}` · scanned **{_rs['total']}** trip(s)"
                + (f" (of {_rs['total_in_feed']} in feed; capped)" if _rs.get("truncated") else "")
            )
            if _rs.get("messages"):
                for _m in _rs["messages"]:
                    st.caption(_m)
            st.caption(
                f"Trips with ≥1 flagged segment: **{_rs['with_flags']}** · "
                f"Build errors: **{_rs['with_errors']}**"
            )
            _df = _rs.get("df")
            if _df is not None and not _df.empty:
                _render_route_scan_results_table(_df)
            elif not _rs.get("error"):
                st.success("No problematic trips in this scan (no segment flags and no build errors).")
            _rs_base = (
                f"RouteScan_{str(_rs['route']).strip()}_{_filename_ddmmyyyy(_rs['date'])}_"
                f"{_filename_in_out_from_route_scan_dirs(_rs['directions'])}"
            )
            _csv_route = _route_scan_csv_text(_rs)
            _df_xlsx = _df if _df is not None and not _df.empty else empty_route_scan_export_df()
            _xlsx_route = build_route_scan_excel_bytes(_df_xlsx, _route_scan_meta_strings(_rs))
            _export_download_row()
            st.download_button(
                "Download route scan (Excel)",
                data=_xlsx_route,
                file_name=f"{_rs_base}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_route_scan_xlsx",
            )
            st.download_button(
                "Download route scan (CSV)",
                data=_csv_route,
                file_name=f"{_rs_base}.csv",
                mime="text/csv",
                key="download_route_scan_csv",
            )

            _all_ids = _rs.get("all_trip_ids") or []
            _scan_rows = _rs.get("scan_rows")
            if (
                _scan_rows
                and len(_scan_rows) > 0
                and not hasattr(_scan_rows[0], "first_dep_raw")
            ):
                _scan_rows = None
            if _all_ids:
                st.divider()
                st.markdown("**Open one trip** — loads segments, map, and trip exports **only for that trip** (not all scanned trips).")
                _rp = st.columns([4, 1, 1])
                with _rp[0]:
                    if _scan_rows and len(_scan_rows) == len(_all_ids):
                        _sorted = sorted(_scan_rows, key=_route_scan_trip_sort_key)
                        _pick_ids = [r.trip_id for r in _sorted]
                        _label_map = {r.trip_id: _route_scan_trip_choice_label(r) for r in _sorted}
                        _trip_pick = st.selectbox(
                            "Choose trip (time · direction · headsign)",
                            options=_pick_ids,
                            format_func=lambda tid: _label_map.get(str(tid), str(tid)),
                            key="route_scan_drill_pick",
                            help=(
                                "Sorted by **direction** then **first departure**. Trip ID is shown at the end. "
                                "Then click **Load trip detail**."
                            ),
                        )
                    else:
                        _trip_pick = st.selectbox(
                            "Choose trip ID",
                            options=sorted(_all_ids),
                            key="route_scan_drill_pick",
                            help="Pick a trip from this scan, then click **Load trip detail**.",
                        )
                with _rp[1]:
                    _load_drill = st.button("Load trip detail", key="route_scan_drill_load", type="primary")
                with _rp[2]:
                    _clear_drill = st.button("Clear", key="route_scan_drill_clear")
                if _load_drill:
                    st.session_state["route_scan_drill_trip_id"] = str(_trip_pick)
                if _clear_drill:
                    st.session_state.pop("route_scan_drill_trip_id", None)

                _allowed = set(_all_ids)
                _drill_id = st.session_state.get("route_scan_drill_trip_id")
                if _drill_id and str(_drill_id) in _allowed:
                    with st.spinner("Loading trip…"):
                        _render_single_trip_drilldown(
                            gtfs_dir,
                            str(_drill_id),
                            _rs["date"],
                            str(_rs["route"]),
                        )
                elif _drill_id and str(_drill_id) not in _allowed:
                    st.caption(
                        "Stored trip is not from the latest scan — run **Scan route** again, "
                        "or click **Load trip detail** after choosing a trip."
                    )

init_audit_widget_defaults()
hydrate_from_url_once()

if "audit_extra_leg_ids" not in st.session_state:
    st.session_state.audit_extra_leg_ids = []
if "audit_extra_leg_count" in st.session_state:
    _legacy_n = int(st.session_state.pop("audit_extra_leg_count") or 0)
    if _legacy_n > 0 and not st.session_state.audit_extra_leg_ids:
        for _ in range(min(_legacy_n, _MAX_AUDIT_EXTRA_LEGS)):
            st.session_state.audit_extra_leg_ids.append(uuid.uuid4().hex[:12])

_audit_extra_leg_ids: list[str] = list(st.session_state.get("audit_extra_leg_ids") or [])

with st.form("audit"):
    c1, c2, c3 = st.columns(3)
    with c1:
        service_date = st.date_input(
            "Service date",
            format="DD/MM/YYYY",
            help="Day / month / year (EU).",
            key="audit_service_date",
        )
    with c2:
        route = st.text_input(
            "Route number",
            placeholder="e.g. 39",
            help="Matches routes.route_short_name (no default — enter your route).",
            key="audit_route",
        )
    with c3:
        direction = st.selectbox(
            "Direction",
            options=[("Outbound (GTFS 0)", 0), ("Inbound (GTFS 1)", 1)],
            format_func=lambda x: x[0],
            help=(
                "GTFS only has direction_id 0 and 1; names are not universal. "
                "In the current Dublin Bus feed, route 39 uses 0 for Ongar and 1 for Burlington Road–side patterns. "
                "If you get no trip or the wrong branch, switch direction."
            ),
            key="audit_direction",
        )
        direction_id = direction[1]

    departure_time = st.text_input(
        "Departure time from terminus",
        placeholder="e.g. 17:32 or 17:32:00",
        help=(
            "Type the scheduled time at the first stop of the trip (stop_sequence minimum). "
            "Use HH:MM or HH:MM:SS. GTFS can use times after midnight (e.g. 25:30:00)."
        ),
        key="audit_departure_time",
    )

    add_leg = st.form_submit_button("Add leg")

    _remove_leg_clicked: dict[str, bool] = {}
    for _leg_idx, _leg_id in enumerate(_audit_extra_leg_ids):
        st.divider()
        st.caption(f"Leg {_leg_idx + 2}")
        _remove_leg_clicked[_leg_id] = st.form_submit_button(
            "Remove leg",
            key=f"audit_rm_{_leg_id}",
        )

        _ec1, _ec2, _ec3 = st.columns(3)
        with _ec1:
            st.date_input(
                "Service date",
                format="DD/MM/YYYY",
                help="Day / month / year (EU).",
                key=f"audit_extra_{_leg_id}_service_date",
            )
        with _ec2:
            st.text_input(
                "Route number",
                placeholder="e.g. 39",
                help="Matches routes.route_short_name (no default — enter your route).",
                key=f"audit_extra_{_leg_id}_route",
            )
        with _ec3:
            st.selectbox(
                "Direction",
                options=[("Outbound (GTFS 0)", 0), ("Inbound (GTFS 1)", 1)],
                format_func=lambda x: x[0],
                help=(
                    "GTFS only has direction_id 0 and 1; names are not universal. "
                    "In the current Dublin Bus feed, route 39 uses 0 for Ongar and 1 for Burlington Road–side patterns. "
                    "If you get no trip or the wrong branch, switch direction."
                ),
                key=f"audit_extra_{_leg_id}_direction",
            )

        st.text_input(
            "Departure time from terminus",
            placeholder="e.g. 17:32 or 17:32:00",
            help=(
                "Type the scheduled time at the first stop of the trip (stop_sequence minimum). "
                "Use HH:MM or HH:MM:SS. GTFS can use times after midnight (e.g. 25:30:00)."
            ),
            key=f"audit_extra_{_leg_id}_departure_time",
        )

    submitted = st.form_submit_button("Run audit")

# st_folium triggers reruns where the form is not "submitted"; keep showing the last good audit.
_removed_leg_id: str | None = None
for _lid, _did_click in _remove_leg_clicked.items():
    if _did_click:
        _removed_leg_id = _lid
        break

if add_leg and len(st.session_state.audit_extra_leg_ids) < _MAX_AUDIT_EXTRA_LEGS:
    st.session_state.audit_extra_leg_ids.append(uuid.uuid4().hex[:12])
if _removed_leg_id is not None:
    st.session_state.audit_extra_leg_ids = [
        x for x in st.session_state.audit_extra_leg_ids if x != _removed_leg_id
    ]
    _audit_clear_extra_leg_session_keys(_removed_leg_id)

if submitted:
    st.session_state["_audit_keep"] = False
if add_leg or _removed_leg_id is not None:
    # Form body runs before these handlers; without an immediate rerun, extra leg
    # widgets would not appear until some other interaction (one-click lag / wrong
    # button behaviour with multiple form submit buttons).
    st.session_state["_audit_keep"] = False
    st.rerun()

can_run = submitted or st.session_state.get("_audit_keep") or st.session_state.get("_audit_restore")
if not can_run:
    st.stop()

_audit_legs = _collect_audit_legs(
    service_date, route, direction, departure_time, _audit_extra_leg_ids
)

if not str(route).strip():
    st.session_state.pop("_audit_restore", None)
    st.warning("Enter a **route number** before running the audit.")
    st.stop()

_url_synced = False
_any_leg_ok = False
_audit_zip_xlsx: list[tuple[str, bytes]] = []
_, trips, _, _ = load_core_tables(gtfs_dir)

for _leg_ix, (_leg_num, _leg_sd, _leg_route, _leg_dir_id, _leg_dep_raw) in enumerate(_audit_legs):
    if len(_audit_legs) > 1:
        if _leg_ix > 0:
            st.divider()
        st.subheader(f"Leg {_leg_num}")

    _hhmm, _dep_err = parse_typed_departure_time(_leg_dep_raw)
    if _dep_err:
        if _leg_num == 1:
            st.session_state.pop("_audit_restore", None)
            st.warning(_dep_err)
            st.stop()
        st.error(f"**Leg {_leg_num}:** {_dep_err}")
        continue
    if not _leg_route.strip():
        if _leg_num == 1:
            st.session_state.pop("_audit_restore", None)
            st.warning("Enter a **route number** before running the audit.")
            st.stop()
        st.error(f"**Leg {_leg_num}:** Enter a **route number**.")
        continue

    _spinner_label = (
        f"Leg {_leg_num}: matching trip and building segments…"
        if len(_audit_legs) > 1
        else "Matching trip and building segments…"
    )
    with st.spinner(_spinner_label):
        trip_id, msgs, _cand = match_trip(
            gtfs_dir,
            service_date=_leg_sd,
            route_short_name=_leg_route,
            direction_id=_leg_dir_id,
            headsign_contains="",
            terminus_departure_hhmm=_hhmm,
        )

    for m in msgs:
        st.caption(m)

    if trip_id is None:
        st.session_state.pop("_audit_restore", None)
        _trip_err = "Could not resolve a single trip. Adjust inputs or check messages above."
        if _leg_num == 1:
            st.error(_trip_err)
            st.stop()
        st.error(f"**Leg {_leg_num}:** {_trip_err}")
        continue

    st_times = stop_times_for_trip(gtfs_dir, trip_id)
    shape_df = shape_for_trip(gtfs_dir, trips, trip_id)
    stops = load_stops(gtfs_dir)

    rows, err = build_segment_table(st_times, stops, shape_df)
    if err:
        st.session_state.pop("_audit_restore", None)
        if _leg_num == 1:
            st.error(err)
            st.stop()
        st.error(f"**Leg {_leg_num}:** {err}")
        continue

    annotate_segment_flags(rows)

    if not _url_synced:
        if st.session_state.get("_audit_restore"):
            st.session_state.pop("_audit_restore", None)
        sync_audit_to_url(_leg_route, _leg_sd, _leg_dir_id, _hhmm)
        _url_synced = True

    _any_leg_ok = True

    st.success(f"Trip **{trip_id}**")

    folium_map = build_route_map(shape_df, st_times, stops)
    if folium_map is not None:
        st.subheader("Route map")
        st.caption(
            "Blue line: GTFS **shape** (published path). Red dots: **stops** on this trip (hover for name). "
            "Map data © OpenStreetMap contributors."
        )
        st_folium(
            folium_map,
            use_container_width=True,
            height=480,
            returned_objects=[],
            key=f"audit_route_map_leg_{_leg_num}",
        )

    direction_label = "Outbound" if int(_leg_dir_id) == 0 else "Inbound"
    terminus_dep_display = format_gtfs_time_display(_hhmm)
    trip_meta: TripMeta = {
        "route": _leg_route.strip(),
        "direction": direction_label,
        "terminus_departure": terminus_dep_display,
        "service_date_display": format_service_date_eu(_leg_sd),
        "day_type": day_type_label(_leg_sd),
    }

    st.markdown(
        f"**Trip** · Route **{trip_meta['route']}** · **{trip_meta['direction']}** · "
        f"Terminus departure **{trip_meta['terminus_departure']}**"
    )

    n_flagged, flag_buckets = flag_summary(rows)
    if n_flagged:
        parts = [f"{k}: {v}" for k, v in sorted(flag_buckets.items(), key=lambda x: (-x[1], x[0]))]
        st.warning("**" + str(n_flagged) + "** segment(s) flagged — " + " · ".join(parts))
    else:
        st.caption("No heuristic flags on this trip (defaults in `auditor/segment_flags.py`).")

    if len(_audit_legs) == 1:
        with st.expander("What flags mean (heuristics)"):
            st.markdown(_AUDIT_FLAGS_EXPANDER_MD)

    df = pd.DataFrame(
        {
            "From stop": [r.from_stop_name for r in rows],
            "To stop": [r.to_stop_name for r in rows],
            "Timetable depart (from)": [format_gtfs_time_display(r.depart_from_scheduled) for r in rows],
            "Timetable arrive (to)": [format_gtfs_time_display(r.arrive_to_scheduled) for r in rows],
            "Distance along shape (m)": [round(r.distance_m, 1) for r in rows],
            "Scheduled time (s)": [r.time_s for r in rows],
            "Scheduled time (M:SS)": [format_duration_m_ss(r.time_s) for r in rows],
            "Implied speed (km/h)": [round(r.speed_kmh, 2) if r.speed_kmh is not None else None for r in rows],
            "Flag(s)": ["; ".join(r.flags) if r.flags else "—" for r in rows],
        }
    )

    st.caption(
        "Each row is one segment to the next stop. **Implied speed** is the **average** for that segment "
        "(shape distance ÷ scheduled time). **Flag(s)** highlights segments that may deserve a second look."
    )

    _render_segment_audit_table(df)

    _base_name = _audit_schedule_export_base_name(_leg_route, _hhmm, _leg_sd, _leg_dir_id)

    csv_buf = StringIO()
    cw = csv.writer(csv_buf)
    cw.writerow(["Route number", trip_meta["route"]])
    cw.writerow(["Direction", trip_meta["direction"]])
    cw.writerow(["Terminus departure (scheduled)", trip_meta["terminus_departure"]])
    cw.writerow(["Service date", trip_meta["service_date_display"]])
    cw.writerow(["Day type", trip_meta["day_type"]])
    cw.writerow([])
    df.to_csv(csv_buf, index=False)

    xlsx_bytes = build_audit_excel_bytes(df, trip_meta)

    if len(_audit_legs) > 1:
        _audit_zip_xlsx.append((f"{_base_name}.xlsx", xlsx_bytes))
        _export_download_row()
        st.download_button(
            "Download Excel (.xlsx)",
            data=xlsx_bytes,
            file_name=f"{_base_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"audit_xlsx_leg_{_leg_num}",
        )
        st.download_button(
            "Download CSV",
            data=csv_buf.getvalue(),
            file_name=f"{_base_name}.csv",
            mime="text/csv",
            key=f"audit_csv_leg_{_leg_num}",
        )
    else:
        _export_download_row()
        st.download_button(
            "Download Excel (.xlsx)",
            data=xlsx_bytes,
            file_name=f"{_base_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="audit_xlsx_single",
        )
        st.download_button(
            "Download CSV",
            data=csv_buf.getvalue(),
            file_name=f"{_base_name}.csv",
            mime="text/csv",
            key="audit_csv_single",
        )

if len(_audit_legs) > 1:
    with st.expander("What flags mean (heuristics)"):
        st.markdown(_AUDIT_FLAGS_EXPANDER_MD)
    if _audit_zip_xlsx:
        _export_download_row()
        _zip_buf = BytesIO()
        with zipfile.ZipFile(_zip_buf, "w", zipfile.ZIP_DEFLATED) as _zf:
            for _zip_fn, _zip_data in _audit_zip_xlsx:
                _zf.writestr(_zip_fn, _zip_data)
        _zip_file_name = (
            f"{_audit_legs[0][2].strip()}_{_filename_ddmmyyyy(_audit_legs[0][1])}_Audit_legs.zip"
        )
        st.download_button(
            "Download all legs (Excel, ZIP)",
            data=_zip_buf.getvalue(),
            file_name=_zip_file_name,
            mime="application/zip",
            key="audit_zip_all_legs_xlsx",
        )

st.session_state["_audit_keep"] = bool(_any_leg_ok)

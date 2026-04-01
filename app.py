"""Streamlit: Dublin Bus scheduled segment speeds (GTFS)."""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from io import StringIO
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
from auditor.segment_flags import annotate_segment_flags, flag_summary
from auditor.segments import build_segment_table
from auditor.excel_export import TripMeta, build_audit_excel_bytes
from auditor.route_map import build_route_map
from auditor.time_util import (
    day_type_label,
    format_duration_m_ss,
    format_gtfs_time_display,
    format_service_date_eu,
    parse_typed_departure_time,
)
from auditor.trip_match import (
    load_core_tables,
    load_stops,
    match_trip,
    shape_for_trip,
    stop_times_for_trip,
)
from auditor.url_state import hydrate_from_url_once, init_audit_widget_defaults, sync_audit_to_url

load_dotenv()

ROOT = Path(__file__).resolve().parent

# Streamlit's HTML table ignores pandas Styler alignment; CSS centers cells.
# Use a display-only frame with formatted strings so floats don't show as 392.500000.
_AUDIT_TABLE_CSS = """
<style>
[data-testid="stDataFrame"] table thead th,
[data-testid="stDataFrame"] table tbody td {
    text-align: center !important;
    vertical-align: middle;
}
</style>
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
st.title("Dublin Bus schedule auditor")
st.caption(
    "Stop-to-stop distance along the published shape, timetabled time, and implied speed (km/h) from GTFS."
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

_render_feed_info(gtfs_dir)

init_audit_widget_defaults()
hydrate_from_url_once()

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
        "First departure from terminus",
        placeholder="e.g. 17:32 or 17:32:00",
        help=(
            "Type the scheduled time at the first stop of the trip (stop_sequence minimum). "
            "Use HH:MM or HH:MM:SS. GTFS can use times after midnight (e.g. 25:30:00)."
        ),
        key="audit_departure_time",
    )

    submitted = st.form_submit_button("Run audit")

# st_folium triggers reruns where the form is not "submitted"; keep showing the last good audit.
if submitted:
    st.session_state["_audit_keep"] = False

can_run = submitted or st.session_state.get("_audit_keep") or st.session_state.get("_audit_restore")
if not can_run:
    st.info("Set inputs and click **Run audit**, or open a bookmarked link with your last audit.")
    st.stop()

if not str(route).strip():
    st.session_state.pop("_audit_restore", None)
    st.warning("Enter a **route number** before running the audit.")
    st.stop()

dep_normalized, dep_parse_err = parse_typed_departure_time(departure_time)
if dep_parse_err:
    st.session_state.pop("_audit_restore", None)
    st.warning(dep_parse_err)
    st.stop()

hhmm = dep_normalized

with st.spinner("Matching trip and building segments…"):
    trip_id, msgs, cand = match_trip(
        gtfs_dir,
        service_date=service_date,
        route_short_name=route,
        direction_id=direction_id,
        headsign_contains="",
        terminus_departure_hhmm=hhmm,
    )

for m in msgs:
    st.caption(m)

if trip_id is None:
    st.session_state.pop("_audit_restore", None)
    st.error("Could not resolve a single trip. Adjust inputs or check messages above.")
    st.stop()

_, trips, _, _ = load_core_tables(gtfs_dir)
st_times = stop_times_for_trip(gtfs_dir, trip_id)
shape_df = shape_for_trip(gtfs_dir, trips, trip_id)
stops = load_stops(gtfs_dir)

rows, err = build_segment_table(st_times, stops, shape_df)
if err:
    st.session_state.pop("_audit_restore", None)
    st.error(err)
    st.stop()

annotate_segment_flags(rows)

st.session_state["_audit_keep"] = True
if st.session_state.get("_audit_restore"):
    st.session_state.pop("_audit_restore", None)
sync_audit_to_url(route, service_date, direction_id, dep_normalized)

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
        key="audit_route_map",
    )

direction_label = "Outbound" if int(direction_id) == 0 else "Inbound"
terminus_dep_display = format_gtfs_time_display(dep_normalized)
trip_meta: TripMeta = {
    "route": route.strip(),
    "direction": direction_label,
    "terminus_departure": terminus_dep_display,
    "service_date_display": format_service_date_eu(service_date),
    "day_type": day_type_label(service_date),
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

with st.expander("What flags mean (heuristics)"):
    st.markdown(
        """
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
    )

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
    "Each row is one segment to the next stop. Distance is along the mapped route; "
    "speed is that distance divided by the timetable time for the segment. "
    "**Flag(s)** highlights segments that may deserve a second look."
)

st.markdown(_AUDIT_TABLE_CSS, unsafe_allow_html=True)
st.dataframe(_audit_table_for_display(df), use_container_width=True, hide_index=True)

base_name = f"{route.strip()}_{hhmm.replace(':', '')}_Audit"

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

dl1, dl2 = st.columns(2)
with dl1:
    st.download_button(
        "Download Excel (.xlsx)",
        data=xlsx_bytes,
        file_name=f"{base_name}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
with dl2:
    st.download_button(
        "Download CSV",
        data=csv_buf.getvalue(),
        file_name=f"{base_name}.csv",
        mime="text/csv",
    )

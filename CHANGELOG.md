# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) where practical.

## [Unreleased]

### Added

### Changed

### Fixed

### Removed

---

## [1.8.0] — 2026-04-06

### Changed

- **In-app positioning**: clearer explanation that implied speed is a **schedule vs shape** **average** per segment; not traffic, posted limits, skip-to-serve, or AVL; why low averages are common vs road limits
- **Route scan → Open one trip**: trip picker shows **first departure · direction · headsign** (trip ID at the end), sorted by direction then departure time — no need to hunt raw trip IDs alone

### Fixed

- **Route scan trip picker**: avoid crash when **session state** still held older `TripScanRow` objects (before `first_dep_raw`); fall back to trip-ID list until the next **Scan route**

---

## [1.7.0] — 2026-04-04

### Changed

- **Export filenames**: include **service date** as `DDMMYYYY` and direction as **`OUT`** / **`IN`** (GTFS 0 / 1); route scan with both directions uses **`IN_OUT`**

---

## [1.6.0] — 2026-04-05

### Added

- **Disclaimer** at the top of the app: implied speed does not model traffic, congestion, turns, roundabouts, signals, or road class

### Changed

- **Export downloads**: spacing above the exports; **Excel** and **CSV** stacked vertically (CSV under Excel)

### Fixed

- **Segment table** and **route scan results** (problem trips list): long cells use a real HTML table (Streamlit’s dataframe grid draws on canvas and clips text); each table sits in the same **bounded box** that scrolls horizontally and **vertically** so long lists do not fill the whole page

### Removed

- Idle-state line: “Set inputs and click Run audit…” before the first run

---

## [1.5.0] — 2026-03-31

### Added

- **Route scan → Open one trip**: after a scan, pick a **trip ID**, click **Load trip detail** to load segments, map, and **per-trip** Excel/CSV for **that trip only** (other scanned trips stay summary-only). **Clear** removes the detail; a **new scan** clears the previous selection

### Notes

- **Implied speed** is **shape distance** between consecutive stops divided by **scheduled** time from departure at stop *i* to arrival at stop *i+1*. It does **not** model road class, turns, roundabouts, traffic signals, or congestion—only what the timetable allows across the GTFS polyline segment

---

## [1.4.0] — 2026-04-04

### Added

- **Sidebar → Route scan**: batch scan all trips for a route + calendar day + direction(s); main-area **Route scan results** table of trips with segment flags or build errors (max **500** trips per run)
- **Route scan export**: **CSV** and **Excel** (summary metadata + problem trips table; empty table if none flagged)

### Changed

- **Route scan** is **enabled on Streamlit Community Cloud** by default (same as local). Opt out with **`HIDE_ROUTE_SCAN=1`** in environment or Streamlit secrets if needed

---

## [1.3.0] — 2026-04-03

### Changed

- **Headsign** removed from the audit form (matching always ignores headsign); URL sync no longer stores `hs`
- **`_env()`** also reads **Streamlit Community Cloud** secrets (`st.secrets`) so `NTA_API_KEY` / `GTFS_DOWNLOAD_URL` work without a `.env` file on Cloud
- **README**: deployment section for **Streamlit Community Cloud** (share.streamlit.io, secrets TOML, first-run GTFS note)

---

## [1.2.0] — 2026-04-02

### Added

- Sidebar **Last downloaded** date and time (**Europe/Dublin**); persisted under `data/current/.gtfs_downloaded_at` after each fetch
- **`Check for update`** (HEAD / lightweight GET): compares remote **ETag** and **Last-Modified** to your last download without fetching the zip; stores **`.gtfs_download_meta.json`** (etag + headers) on each full download
- **`tzdata`** dependency so `Europe/Dublin` works reliably on Windows

### Changed

- GTFS feed summary block uses Streamlit’s bordered container (theme-aware light/dark)
- Segment flags: **slow implied speed on long segment** (≥1 km shape distance, implied under 38 km/h) to highlight through-legs with very low averages; trip-relative slow unchanged

---

## [1.1.0] — 2026-04-01

### Added

- **README** with setup, `streamlit run`, env vars, project layout, GTFS refresh behaviour, and brief deployment notes
- **`.env.example`** for `NTA_API_KEY` and `GTFS_DOWNLOAD_URL`
- **Segment flags** (`Flag(s)` column in the table, CSV, and Excel): **tight schedule** (implied speed ≥ 55 km/h), **no schedule time**, **tiny shape distance** on the shape polyline
- **Trip-relative “slow”** flag: **slower than typical for this trip** — compares each segment to the **median** implied speed on the **same** trip; skips comparison when the trip median is below a floor (uniformly slow / congested patterns)
- UI: summary when any segment is flagged; expander describing flag rules; thresholds live in `auditor/segment_flags.py`

### Changed

- **Dropped** a fixed absolute “slow km/h” rule (it flagged natural slow running in dense areas); replaced with the median-based trip-relative check above

---

## [1.0.0] — 2026-03-31

First public release of the Dublin Bus schedule auditor.

### Added

- Streamlit **single-page** app: route, direction (GTFS 0/1), typed **first departure from terminus**, optional **headsign**, **service date** (EU date picker)
- **GTFS** download to `data/current/` with chunked `stop_times` reads for large feeds
- **Trip matching** via `calendar` / `calendar_dates`, route, direction, `shape_id`, first-stop departure time
- **Segment geometry**: Shapely line from `shapes.txt`, Irish Grid (EPSG:2157) distances, shape orientation, numeric sort for `shape_pt_sequence` / `stop_sequence`
- **Table**: from/to stops, timetable depart/arrive, distance (m), time (s), **M:SS** duration, implied speed (km/h)
- **Trip** summary line; **URL query params** to restore inputs and re-run after refresh
- **Session state** for folium reruns and last successful audit
- **Route map** (Folium + streamlit-folium): shape polyline + stop markers
- **Exports**: CSV and formatted **Excel** (trip meta block + segment table, filters, freeze panes)
- Excel/CSV meta: route, direction, terminus departure, **service date (DD/MM/YYYY)**, **day type** (weekday/weekend)
- **`.env`** support for optional API key and GTFS URL override

### Notes

- **No** automatic GTFS refresh on a schedule; manual **Download / refresh** in the UI.

[Unreleased]: https://github.com/robertf670/profileissues/compare/v1.8.0...HEAD
[1.8.0]: https://github.com/robertf670/profileissues/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/robertf670/profileissues/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/robertf670/profileissues/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/robertf670/profileissues/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/robertf670/profileissues/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/robertf670/profileissues/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/robertf670/profileissues/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/robertf670/profileissues/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/robertf670/profileissues/releases/tag/v1.0.0

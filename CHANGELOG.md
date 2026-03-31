# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) where practical.

## [Unreleased]

### Added

- _(Add new bullets here before each release.)_

### Changed

### Fixed

### Removed

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

[Unreleased]: https://github.com/robertf670/profileissues/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/robertf670/profileissues/releases/tag/v1.0.0

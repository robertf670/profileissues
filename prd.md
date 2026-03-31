# PRD: Dublin Bus Schedule Flaw Auditor

## 1. Background & objective

**Audience:** Dublin Bus drivers (and anyone auditing published timetables).

**Problem:** The timetable implies a **required speed** between stops. Example: ~600 m in 5 minutes implies ~7.2 km/h — often unreasonable and correlates with forced holding or slack baked into the schedule.

**Objective:** Build a **Python** tool that, for **one chosen trip**, computes **scheduled** stop-to-stop **distance** (along the road), **time allowed**, and implied **speed (km/h)** for **every** segment, using **current NTA GTFS** data.

**Out of scope for v1:** Automatic “red/yellow” thresholds for unreasonable speeds (may return later as configurable flags).

---

## 2. Data source & freshness

- **Source:** NTA GTFS static data via the **TFI Open Data API** (Dublin Bus bundle).
- **Authentication:** User’s API key in a **`.env`** file (e.g. `NTA_API_KEY` or as documented for that API).
- **Download:** Fetch the latest **`GTFS_Dublin_Bus.zip`**, unzip to **`data/current/`** (replace previous extract so the app always reflects the **latest published** schedule).
- **Why freshness matters:** Feeds update often; after a new publish, trips and times can differ from yesterday for the **same** calendar day. The app should assume **“latest zip + chosen date”** when resolving services and trips.
- **Provenance:** Show **when** data was downloaded (and **feed_info** date range if present in `feed_info.txt`) so users know which schedule file they are auditing.

**Memory:** `stop_times.txt` can be very large (~500MB+). Use **pandas `read_csv` with `chunksize`** (and/or filter early) so loads stay safe. Filter by **relevant `trip_id`s** once routes/trips/calendar have been narrowed.

---

## 3. Trip selection (user inputs)

Users do **not** pick individual stops. They define **which trip** to audit:

| Input | Definition |
|--------|----------------|
| **Route** | Public route number (e.g. `39`) — match GTFS `routes.route_short_name` (and resolve `route_id`). |
| **Direction** | **Inbound** or **Outbound** — maps to GTFS `trips.direction_id` (document which value is which in code/UI). |
| **Departure time** | Scheduled **first departure of the trip from the terminus** — i.e. `departure_time` at **first** `stop_times` row for that trip (`stop_sequence` minimum). Format e.g. `17:32` (handle `HH:MM:SS` in feed). |
| **Destination (headsign)** | Trip **headsign** text (e.g. “Ongar”) — match against `trips.trip_headsign` (case-insensitive; substring or normalized match as implemented). |
| **Service day** | A **calendar date** the user cares about — use `calendar` / `calendar_dates` in the **current** feed to resolve which `service_id`s run that day, then intersect with `trips.service_id`. |

**Output:** Exactly **one** `trip_id` when inputs are unique; if ambiguous, the UI should list candidates or ask for refinement (implementation detail).

---

## 4. Core calculations

### 4.1 Road distance (not straight-line)

1. From `trips.txt`: `trip_id` → `shape_id`.
2. From `shapes.txt`: build a **Shapely `LineString`** of shape points (order by `shape_pt_sequence`).
3. From `stops.txt`: stop coordinates for each stop on the trip.
4. **Project** each stop onto the line; compute **cumulative distance along the line** to each projected point; segment length = difference between consecutive stops **along the shape** (meters → display in m; speeds in km/h).

**Edge cases:** If projection is degenerate, define a fallback (e.g. nearest point on line; document behaviour).

### 4.2 Time per segment

For consecutive stops A → B on that trip:

- `time_gap = arrival_time(B) − departure_time(A)` (GTFS times; handle **>24h** trips if present).
- Convert to hours for speed.

### 4.3 Speed

- `speed_kmh = (segment_length_km) / (time_gap_hours)`  
- Example: 0.6 km in 5 min → 0.6 / (5/60) ≈ **7.2 km/h**.

---

## 5. User interface

- **Stack:** **Streamlit** — single-page app: inputs above + **auditor table** for **all** segments of the selected trip.
- **Table columns (minimum):** from stop A → to stop B, distance (m), time allowed (min), scheduled speed (km/h); optional stop names/IDs from GTFS.
- **Export (optional v1):** Export the table as **CSV** (and PDF later if desired); filename pattern e.g. `{route}_{departure_time}_Audit.csv`.

---

## 6. Non-goals (v1)

- Real-time AVL or historical GPS (schedule-only).
- Fixed global thresholds (9 / 45 km/h) — not required; flagging can be a later iteration with **user-configurable** limits.

---

## 7. Success criteria (v1)

- User can enter route, direction, terminus departure time, headsign, and date; get **one trip** and a **full** segment table with **shape-based** distances and implied speeds from the **latest** downloaded GTFS.

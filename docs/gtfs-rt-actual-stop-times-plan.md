# Plan: Actual stop arrival / departure times (GTFS-RT)

**Status:** planning only — not implemented  
**Related product:** Profile Issues / Dublin Bus schedule auditor  
**Audience:** maintainers building a “what did the bus actually do?” layer on top of the existing schedule auditor

---

## 1. Goal

### 1.1 Primary user story

> I select the same trip I already audit today — e.g. **route 39**, **outbound**, **terminus departure 17:32**, **service date today**.  
> It is now **20:00**; the trip has finished.  
> I want a stop-by-stop table of **actual arrival** and **actual departure** for that bus, and for any stop I care about I want to see that it **arrived at T** and **departed at T + 2 minutes** (dwell ≈ 2 min).

### 1.2 Secondary goals

- Show **scheduled vs actual** arrival/departure and delay per stop.
- Show **dwell** per stop (`actual_departure − actual_arrival`) when both are known.
- Reuse the existing trip selection UX (`match_trip`: route, direction, terminus time, service date).
- Make gaps explicit: “no RT archive for this trip”, “arrival unknown”, “departure only”, etc.

### 1.3 Non-goals (for this feature set)

- Replacing the schedule auditor (implied speed from static GTFS stays as-is).
- Official legal proof for disputes (open RT is evidence-grade for analysis, not a Dublin Bus AVL export).
- Reconstructing trips from before we started archiving (NTA does not publish a public historical API).
- Inferring passenger boarding, skip-to-serve intent, or driver notes.
- Full network analytics / ML delay prediction.

---

## 2. Why this cannot be “just an API call at 20:00”

NTA / TFI expose **live** GTFS-Realtime feeds. They do **not** expose a public “give me yesterday’s trip” history endpoint.

| Feed | Typical URL | What it is |
|------|-------------|------------|
| TripUpdates | `https://api.nationaltransport.ie/gtfsr/v2/TripUpdates` | Predicted/observed stop times & delays for active (and recently updated) trips |
| VehiclePositions | `https://api.nationaltransport.ie/gtfsr/v2/Vehicles` | Lat/lon (and related fields) for vehicles currently reporting |

Once a trip drops out of the live feed, **querying at 20:00 cannot recover** what happened at 17:45 unless **we stored snapshots while the trip was live**.

**Design consequence:** this feature is two products glued together:

1. **Collector** — always-on (or at least running for the whole service day) poller that archives RT.
2. **Viewer** — Streamlit (or CLI) UI that resolves a static `trip_id` and reads the archive.

Without (1), (2) can only show “no data”.

---

## 3. End-to-end experience (target UX)

### 3.1 Inputs (same as schedule audit)

Reuse today’s form concepts:

| Input | Source |
|-------|--------|
| Route short name | e.g. `39` |
| Direction | Outbound / Inbound → GTFS `direction_id` |
| Departure time from terminus | e.g. `17:32` |
| Service date | calendar day in Europe/Dublin |
| Optional headsign filter | if needed for disambiguation |

Resolve to one static GTFS `trip_id` via existing `auditor.trip_match.match_trip` against **`data/current/`** (or a **versioned** static snapshot keyed to the archive day — see §7).

### 3.2 Output table (per stop, in `stop_sequence` order)

Minimum columns:

| Column | Meaning |
|--------|---------|
| Stop sequence | GTFS |
| Stop name / id | From static `stops.txt` |
| Scheduled arrival | Static `stop_times.arrival_time` |
| Scheduled departure | Static `stop_times.departure_time` |
| Actual arrival | Best estimate from archive (see §5) |
| Actual departure | Best estimate from archive |
| Arrival delay | Actual − scheduled (seconds / minutes) |
| Departure delay | Actual − scheduled |
| Dwell | Actual departure − actual arrival (when both present) |
| Confidence / source | e.g. `trip_update.event`, `trip_update.delay+schedule`, `vehicle_dwell`, `unknown` |
| Notes | skipped, no observations, schedule mismatch, etc. |

### 3.3 “Prove 2 minutes at stop X”

UI affordances:

- Highlight rows where `dwell >= 120s` (configurable).
- Detail drawer for one stop: timeline of evidence (RT events / GPS samples used).
- Export CSV/Excel parallel to the schedule audit exports.

Example row the user wants:

- Stop: *Somewhere Road*
- Actual arrival: `17:51:08`
- Actual departure: `17:53:10`
- Dwell: `2:02`

---

## 4. Data sources and what each can prove

### 4.1 TripUpdates (primary for stop times)

GTFS-RT `FeedEntity.trip_update` → `stop_time_update[]` may include, per stop:

- `arrival.time` / `arrival.delay`
- `departure.time` / `departure.delay`
- `schedule_relationship` (e.g. skipped)
- `stop_sequence` and/or `stop_id`

**Ideal case:** both `arrival.time` and `departure.time` present → dwell is trivial.

**Common real-world case:** only **departure** delay/time is populated for many bus feeds; arrival is missing. Then:

- We can still show **actual departure** (delay applied to scheduled departure, or absolute `time`).
- **Actual arrival** and **dwell** need a fallback (§5.2 VehiclePositions) or stay “unknown”.

**Important:** TripUpdates are often **predictions that firm up** as the vehicle approaches. The archive must keep **history over time**, not only the last snapshot — or at least keep the **final** observation per stop after the vehicle has passed (see §5.1).

### 4.2 VehiclePositions (primary for dwell / movement)

`FeedEntity.vehicle` typically includes:

- `trip.trip_id` (when assigned)
- `position.latitude` / `longitude`
- `timestamp`
- optional `speed`, `bearing`, `current_stop_sequence`, `current_status`, `vehicle.id`

Use cases:

- Reconstruct a breadcrumb trail for the trip.
- Detect **dwell near a stop** when TripUpdates lack arrival/departure pairs.
- Sanity-check RT stop times (bus was physically near the stop when we claim it arrived).

### 4.3 Static GTFS (identity + schedule baseline)

Already in-repo:

- Trip identity via `match_trip`
- Scheduled times via `stop_times`
- Stop coordinates via `stops.txt`
- Shape / map via existing `route_map` / segments (optional overlay of actual path later)

NTA guidance: **RT entity IDs must be joined only to the matching generation of static GTFS**. Archiving RT without pinning the static feed version used that day risks broken joins after a schedule publish.

---

## 5. Algorithms

### 5.1 Deriving actual stop times from TripUpdates archive

For each `(service_date, trip_id, stop_sequence)`:

1. Collect all archived `stop_time_update` records for that stop across poll times.
2. Prefer fields in this order when building **final** actuals:
   - Absolute `arrival.time` / `departure.time` (Unix) if present.
   - Else `delay` + static scheduled time for that stop (convert GTFS clock time + service date → Europe/Dublin datetime; handle `>24:00:00`).
3. **Finalisation rule** (pick one and document in code):
   - **A (recommended v1):** last observation **after** the vehicle’s `current_stop_sequence` has passed this stop, or last observation within a “trip complete” window; or
   - **B:** first observation where delay/time stops changing for N polls; or
   - **C:** observation closest to scheduled time + known delay once `ScheduleRelationship` / age indicates past stop.
4. Mark confidence:
   - `high` — absolute `time` fields present after pass
   - `medium` — delay + schedule only
   - `low` — stale prediction (vehicle not yet at stop when last seen)

Store both **raw events** and a **derived final** row so the UI can explain itself.

### 5.2 Deriving arrival / departure / dwell from VehiclePositions

For each stop with lat/lon:

1. Define a **geofence** radius (start ~30–50 m; tune per urban GPS noise). Optionally use distance-along-shape to the stop’s projection (reuse segment geometry ideas from `auditor/segments.py`).
2. Walk the trip’s position samples in time order.
3. **Arrival candidate:** first sample that enters the geofence (or first with `current_status = STOPPED_AT` for that stop if the feed provides it).
4. **Departure candidate:** last sample before a sustained exit from the geofence (or transition from `STOPPED_AT` to `IN_TRANSIT_TO`).
5. **Dwell** = departure − arrival.
6. Reject dwells that are implausible (e.g. &gt; 30 min at a non-terminus without positions) or that look like GPS jitter (enter/exit every few seconds) — require minimum consecutive samples or minimum stationary duration.

**Merge policy with TripUpdates:**

| Situation | Use |
|-----------|-----|
| TripUpdates has arrival + departure | Prefer TripUpdates; show VehiclePositions as corroboration |
| TripUpdates has departure only | Use TripUpdates departure; estimate arrival from VehiclePositions if possible |
| TripUpdates missing stop | VehiclePositions only |
| Neither | Unknown |

### 5.3 Matching live entities to our trip

1. Resolve static `trip_id` from user inputs + **the static GTFS snapshot for that service date**.
2. In the archive, filter `trip_update.trip.trip_id == trip_id` and/or `vehicle.trip.trip_id == trip_id`.
3. Handle extras if present:
   - `start_date` (YYYYMMDD) — must equal service date
   - `route_id` — cross-check
   - `vehicle.id` — once known, can follow positions even if trip descriptor blanks intermittently

If multiple vehicle IDs appear for one trip_id, keep both and flag ambiguity.

---

## 6. Architecture

```
┌─────────────────────┐     poll 15–30s      ┌──────────────────────────────┐
│ NTA GTFS-RT         │ ──────────────────► │ Collector process            │
│ TripUpdates         │                     │  - fetch protobuf/JSON       │
│ VehiclePositions    │                     │  - parse entities            │
└─────────────────────┘                     │  - write append-only store   │
                                            └──────────────┬───────────────┘
                                                           │
                                                           ▼
                                            ┌──────────────────────────────┐
                                            │ Archive store                │
                                            │  raw polls + derived facts   │
                                            │  + static GTFS snapshot pin  │
                                            └──────────────┬───────────────┘
                                                           │
                                                           ▼
┌─────────────────────┐     match_trip       ┌──────────────────────────────┐
│ Streamlit app       │ ──────────────────► │ Actual-times query layer     │
│ (existing audit UX) │                     │  - load finals for trip_id   │
└─────────────────────┘                     │  - merge schedule + actuals  │
                                            └──────────────────────────────┘
```

### 6.1 Why the collector is separate from Streamlit

- Streamlit Community Cloud **sleeps** and is not a reliable cron.
- Polling every 15–30s for the whole Dublin Bus fleet is a **long-running** job.
- Local `streamlit run` exiting must not stop the archive if we care about evening lookups.

**Recommended:** a small CLI module, e.g. `python -m auditor.rt_collector`, runnable via systemd / Task Scheduler / Docker / a cheap always-on host. Streamlit only **reads** the store.

### 6.2 Suggested package layout (future implementation)

```
auditor/
  rt/
    __init__.py
    client.py          # HTTP fetch + API key headers
    parse.py           # protobuf/JSON → typed records
    store.py           # persistence API
    derive_stop_times.py
    derive_dwell.py
  rt_collector.py      # __main__ loop
docs/
  gtfs-rt-actual-stop-times-plan.md   # this file
data/
  current/             # static GTFS (existing)
  rt/                  # gitignored archive (new)
```

### 6.3 Dependencies (likely)

- Existing: `requests`, `pandas`, `python-dotenv`
- New: `gtfs-realtime-bindings` (protobuf) **or** use `?format=json` if the NTA endpoint reliably supports it
- Optional: SQLite via stdlib `sqlite3` (prefer over ad-hoc CSV for queryability)

Confirm auth header against the developer portal. Transitland documents **`x-api-key`** for these RT URLs; the static zip path in this repo currently uses `Ocp-Apim-Subscription-Key` when a key is set. **Do not assume one header works for both** — probe with the user’s key during implementation spike (§10).

---

## 7. Storage design

### 7.1 Principles

- **Append-only raw polls** for auditability (“show your working”).
- **Derived tables** for fast UI reads.
- **Pin static GTFS** used for joining on that day.
- Keep raw retention configurable (disk fills up quickly at fleet-wide 15s polls).

### 7.2 Suggested SQLite schema (v1 sketch)

**`rt_poll`**

| Column | Notes |
|--------|-------|
| poll_id | PK |
| feed | `trip_updates` / `vehicles` |
| fetched_at | UTC |
| feed_header_timestamp | from GTFS-RT header if present |
| http_status / error | nullable |
| payload_path or blob | optional: store file on disk, hash in DB |

**`rt_trip_update_event`**

| Column | Notes |
|--------|-------|
| poll_id | FK |
| observed_at | UTC |
| trip_id | |
| start_date | nullable |
| route_id | nullable |
| vehicle_id | nullable |
| stop_id | nullable |
| stop_sequence | nullable |
| arrival_time | nullable Unix |
| arrival_delay | nullable seconds |
| departure_time | nullable Unix |
| departure_delay | nullable seconds |
| schedule_relationship | nullable |

**`rt_vehicle_position`**

| Column | Notes |
|--------|-------|
| poll_id | FK |
| observed_at | UTC (prefer vehicle.timestamp) |
| trip_id | nullable |
| start_date | nullable |
| vehicle_id | nullable |
| lat / lon | |
| speed | nullable |
| bearing | nullable |
| current_stop_sequence | nullable |
| current_status | nullable |

**`static_feed_pin`**

| Column | Notes |
|--------|-------|
| service_date | |
| pinned_at | when we copied/hashed |
| gtfs_source_etag / last_modified | from existing download meta if available |
| content_hash | hash of critical files (`trips`, `stop_times`, `stops`, …) |
| snapshot_path | e.g. `data/rt/static_pins/YYYYMMDD/` |

**`actual_stop_time` (derived)**

| Column | Notes |
|--------|-------|
| service_date | |
| trip_id | |
| stop_sequence | |
| stop_id | |
| scheduled_arrival / scheduled_departure | |
| actual_arrival / actual_departure | nullable |
| dwell_seconds | nullable |
| source | enum |
| confidence | enum |
| derived_at | |
| evidence_json | small pointer/summary to supporting poll ids |

### 7.3 Volume rough order

Fleet-wide VehiclePositions + TripUpdates every 20s → large JSON/protobuf payloads. Mitigations:

- Optionally filter to **watched routes** (e.g. only `39`) in early versions.
- Compress raw payloads (gzip) or keep only parsed rows after N days.
- Nightly job: derive `actual_stop_time`, then prune raw polls older than R days.

For the user’s story (“I care about the 39 I drove today”), **route-filtered collection** is a strong v1 cost control — with the caveat that you must have been collecting that route during the trip.

### 7.4 Git / deploy

- Extend `.gitignore` with `data/rt/`.
- Never commit API keys; extend `.env.example` with RT-specific vars.

---

## 8. Configuration

Proposed env / secrets (names TBD during implementation):

| Variable | Purpose |
|----------|---------|
| `NTA_API_KEY` | Existing key; reuse if portal key works for RT |
| `GTFSR_TRIP_UPDATES_URL` | Default `https://api.nationaltransport.ie/gtfsr/v2/TripUpdates` |
| `GTFSR_VEHICLES_URL` | Default `https://api.nationaltransport.ie/gtfsr/v2/Vehicles` |
| `GTFSR_API_KEY_HEADER` | e.g. `x-api-key` (confirm in spike) |
| `GTFSR_POLL_SECONDS` | Default `20` |
| `GTFSR_STORE_PATH` | Default `data/rt/archive.sqlite` |
| `GTFSR_ROUTE_ALLOWLIST` | Optional comma list, e.g. `39,39a` |
| `GTFSR_RAW_RETENTION_DAYS` | e.g. `14` |
| `GTFSR_GEOFENCE_METERS` | e.g. `40` |

Streamlit secrets should mirror local `.env` the same way `_env()` already does for GTFS download.

---

## 9. UI integration plan

### Phase-friendly placement

**Option A (recommended):** new main-page section or tab — **“Actual times (archived RT)”** — under the existing audit results for the matched trip.

**Option B:** sidebar mode toggle — Schedule audit | Actual times.

Behaviour:

1. User runs the usual trip match (or reuses last matched `trip_id`).
2. App queries `actual_stop_time` for `(service_date, trip_id)`.
3. If empty:
   - Explain: *No archive. The collector must have been running while this trip was live.*
   - Show collector status: last poll time, routes watched, disk path.
4. If partial: render table with unknowns; badge dwell only where both actuals exist.
5. Exports: `{route}_{HHMM}_{DDMMYYYY}_{OUT|IN}_ActualTimes.csv` / `.xlsx`.

Map (later): scheduled shape + stop markers; optional actual path polyline from VehiclePositions; stop popup with actual arr/dep/dwell.

---

## 10. Delivery phases

### Phase 0 — Spike (half-day engineering, blocks everything)

- Authenticate to TripUpdates + Vehicles with the existing key.
- Confirm header name, JSON vs protobuf, field population for Dublin Bus (especially **arrival vs departure**).
- Capture 10–15 minutes of a live trip and manually answer: *can we see a 2-minute dwell from RT alone?*
- Document findings in this file or a short appendix.

**Exit criteria:** sample payloads saved under `data/rt/spike/` (gitignored) and a written note on which fields are trustworthy.

### Phase 1 — Collector MVP

- Poll TripUpdates (+ Vehicles if spike says we need them for dwell).
- Persist parsed events to SQLite.
- Pin/copy static GTFS hash for the day when collector starts / after GTFS refresh.
- CLI: start / status / once (single poll for debugging).
- Route allowlist support.

### Phase 2 — Derivation + query API

- Build `actual_stop_time` for completed / stale trips.
- Pure functions with unit tests using recorded spike fixtures (no live network in CI).
- CLI: `show-trip --route 39 --direction 0 --dep 17:32 --date YYYY-MM-DD`.

### Phase 3 — Streamlit viewer

- Table + dwell highlight + scheduled vs actual.
- Empty-state education.
- CSV/Excel export.
- CHANGELOG entry under `[Unreleased]` when user-visible.

### Phase 4 — Hardening

- Retention / compaction.
- Schedule-version mismatch warnings (static pin hash ≠ current `data/current`).
- Multi-leg audit parity (actual times per leg).
- Optional: watchlist of trips for the driver’s day (“record these terminus deps”).

---

## 11. Edge cases and risks

| Risk | Mitigation |
|------|------------|
| No public history | Honest UX; collector required |
| TripUpdates without arrival times | Vehicle geofence dwell; show departure-only clearly |
| Static GTFS republish changes `trip_id`s | Pin static snapshot per day; warn on mismatch |
| Streamlit Cloud not always-on | External collector; document hosting |
| Poll gaps (network / sleep) | Record gaps; widen uncertainty; don’t invent times |
| GPS jitter false dwells | Min samples, hysteresis geofence, max speed threshold |
| Skipped stops | Honour `schedule_relationship`; don’t fabricate dwell |
| Trip cancelled / reassigned vehicle | Surface `schedule_relationship` + vehicle id changes |
| Clock / timezone | Store UTC; display Europe/Dublin; reuse GTFS &gt;24h handling from `time_util` |
| API rate limits / ToS | Respect portal usage policy; backoff; don’t poll faster than needed |
| “Proof” expectations | Label as open-data reconstruction, not operator AVL certificate |

---

## 12. Testing strategy

1. **Fixtures:** anonymised/truncated protobuf or JSON slices from Phase 0.
2. **Unit tests:** delay+schedule → datetime; geofence arrival/departure; merge policy.
3. **Integration (optional, manual):** collector against live API behind env flag.
4. **Golden trip:** one known trip where a human notes “held ~2 min at stop X”; compare derived dwell.

---

## 13. Success criteria

- [ ] For a trip collected end-to-end, user can open the app after finish and see a stop table with actual departures for most served stops.
- [ ] Where arrival + departure are both evidenced, dwell is shown within ~15–30s of ground truth for a deliberate hold (tolerance TBD after spike).
- [ ] User can answer: “Did I stop ~2 minutes at stop X?” with **yes / no / unknown** and visible evidence source.
- [ ] If collector was off, UI explains why — never silently shows scheduled times as actual.
- [ ] Static schedule audit remains unchanged and available without RT.

---

## 14. Consistency with current repo

| Existing piece | Reuse |
|----------------|-------|
| `match_trip` | Resolve `trip_id` from route / direction / terminus time / date |
| `stop_times_for_trip` | Scheduled baseline columns |
| `time_util` | Parse times, &gt;24h, duration formatting |
| `download.py` / feed meta | ETag / last download for static pin metadata |
| `excel_export` patterns | Actual-times workbook |
| App disclaimer | Extend: actual times come from archived GTFS-RT, not AVL export |
| `prd.md` non-goal | This plan **extends** beyond v1; update PRD when implementation starts |
| `CHANGELOG.md` | Document user-visible RT features when shipped; keep this plan doc link in README if desired |

---

## 15. Implementation spike checklist (first concrete steps)

1. Register/confirm GTFS-Realtime product on [developer.nationaltransport.ie](https://developer.nationaltransport.ie/).
2. `curl`/Python fetch TripUpdates and Vehicles with the existing key; note working header.
3. Dump one entity that matches a known live Dublin Bus `trip_id` from current static GTFS.
4. Inspect whether `stop_time_update` includes both arrival and departure for mid-route stops.
5. Decide v1 scope: **TripUpdates-only** vs **TripUpdates + Vehicles**.
6. Only then implement collector schema.

---

## 16. Open questions

1. Should v1 collect **all Dublin Bus** or only an allowlist (cost vs convenience)?
2. Where will the collector run day-to-day (local PC, NAS, VPS)?
3. Is “actual arrival” required for every stop, or is **departure + dwell only when provable** enough for v1?
4. Do we need multi-day retention for dispute reviews (e.g. 30–90 days)?
5. Should actual times appear in the same Excel as the schedule audit, or as a sibling export?
6. JSON format support vs protobuf — which is stable on NTA v2 for us?

---

## 17. Summary

To search **route 39 / outbound / 17:32** at **20:00** and see **arrived T / departed T+2m**, we must:

1. **Archive** NTA GTFS-RT while the trip runs (TripUpdates ± VehiclePositions).  
2. **Pin** the matching static GTFS generation.  
3. **Derive** per-stop actual arrival/departure/dwell with explicit confidence.  
4. **Query** via the same trip matching the schedule auditor already uses.

This document is the blueprint; no application behaviour changes until Phases 0–3 are implemented.

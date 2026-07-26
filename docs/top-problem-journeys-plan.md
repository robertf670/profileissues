# Plan: Top problem journeys (day / network scan)

**Status:** planning only — not implemented  
**Related product:** Profile Issues / Dublin Bus schedule auditor  
**Builds on:** existing **Route scan** (`auditor/route_scan.py`) and segment flags (`auditor/segment_flags.py`)

---

## 1. Goal

### 1.1 Primary user story

> It is **Sunday**. I open the site, load the current GTFS, and ask:  
> **“What are the top 20 most unrealistic journeys today?”**  
> Optionally I restrict to routes I care about (e.g. `39, 39a, 140`).  
> I get a ranked list, then open any row into the full segment audit (map, flags, export).

### 1.2 Secondary goals

- Same calendar-day semantics as today (`calendar` / `calendar_dates` → services running on that date).
- Same definition of “problem” as the single-trip auditor (implied speed vs shape + existing heuristics) — not a new mystery metric.
- Filters: route allowlist, direction(s), flag-type focus (tight vs padding vs any).
- Export the Top X summary (CSV / Excel).
- Clear progress and honest limits on Streamlit Community Cloud.

### 1.3 Non-goals (v1)

- Live AVL / “actually late today” (that is the GTFS-RT plan; this feature is **schedule-only**).
- Automatic “the schedule is wrong” legal claims — heuristics only.
- Replacing single-route **Route scan** (keep it; Top X is the network / multi-route entry point).
- Perfect road-class / speed-limit modelling (still out of scope for GTFS-only data).

---

## 2. How this relates to what exists

| Today | Gap |
|-------|-----|
| **Run audit** — one trip | No “what should I look at?” |
| **Route scan** — all trips on **one** route for a day; lists trips with ≥1 flag; cap **500** trips | No cross-route ranking; no Top N; weak “severity” (flag count only) |
| **Flags** — tight ≥55 km/h; slow long ≥1 km &lt;38 km/h; slower than trip median; tiny distance; no schedule time | Good building blocks; need a **score** to rank across the network |

**Design stance:** implement Top problem journeys as a **ranked multi-route (or all-route) scan**, reusing `scan_trips_for_flags` / `build_segment_table` / `annotate_segment_flags`, plus a new ranking layer.

---

## 3. Target UX

### 3.1 Inputs

| Input | Notes |
|-------|--------|
| Service date | Default today (Europe/Dublin) |
| Top N | e.g. 10 / 20 / 50 (cap hard, e.g. max 100 in UI) |
| Routes | Empty = all routes in feed (with safety caps); or allowlist `39, 39a, 140` |
| Direction | Outbound / Inbound / both |
| Focus | **Any flag** · **Tight only** · **Slow / padding only** · **Build errors only** |
| Rank by | See §5 (default: severity score) |
| Optional | Deduplicate by pattern (§6) |
| Optional | Time window on terminus departure (e.g. only 06:00–10:00) |

Placement options:

- **A (recommended):** Sidebar block under Route scan — **“Top problem journeys”**.
- **B:** Main-page expander / tab so Cloud users see it without hunting.

Mirror Route scan’s `HIDE_ROUTE_SCAN` pattern if needed (`HIDE_TOP_PROBLEMS=1`) for public deploys that cannot afford the CPU.

### 3.2 Results table (Top N rows)

| Column | Source |
|--------|--------|
| Rank | 1…N |
| Route | `route_short_name` |
| Direction | Out / In |
| First departure | Terminus dep |
| Headsign | |
| Score | Numeric severity |
| Flagged segments | Count |
| Worst segment | Short blurb: stops + implied km/h + flag type |
| Trip ID | For power users |
| Error | If build failed (optionally separate “failed builds” list) |

Actions:

- **Open trip** → existing detail renderer (segment table, map, per-trip export).
- **Download Top N** CSV / Excel.
- Caption: feed download time, service date, routes scanned, trips considered, trips flagged, elapsed time, whether capped.

### 3.3 Empty / partial states

- No GTFS loaded → prompt Download / refresh.
- No services that day → calendar message (same as route scan).
- Scan hit trip cap before finishing network → warn: “Results are from first K trips / incomplete network; narrow routes or raise cap on a stronger host.”
- Zero flagged trips → success empty state, not an error.

---

## 4. Pipeline

```
Service date + route filter + directions
        │
        ▼
List candidate trip_ids (shaped trips only)
        │
        ▼
Optional cap / sample / prioritise (§7)
        │
        ▼
For each trip: build_segment_table → annotate_segment_flags
        │
        ▼
Score trip (§5) + keep worst-segment evidence
        │
        ▼
Filter by focus → sort → take Top N
        │
        ▼
UI table + open detail + export
```

Reuse as much as possible:

- `list_trip_ids_for_route_day` → generalise to **many routes** or **all routes**.
- `scan_trips_for_flags` → extend return type with score fields **or** wrap with a richer dataclass `ProblemTripRow`.
- Progress callback already exists (`on_progress`) — wire to `st.progress`.

---

## 5. Ranking / scoring

Flag **count alone is not enough**: a long trip with many mild “slower than typical” flags can outrank one absurd 90 km/h leg.

### 5.1 Recommended default score (v1)

Compute a non-negative **severity score** per trip from its segments:

| Evidence | Points (tunable) |
|----------|------------------|
| Each **Tight schedule** (≥55 km/h) | `10 + max(0, speed − 55)` (so 80 km/h ≫ 56 km/h) |
| Each **Slow implied on long segment** | `8 + max(0, 38 − speed)` scaled by distance/1000 m |
| Each **Slower than typical for this trip** | `3` (lower weight — relative, noisier) |
| **Tiny shape distance** | `2` |
| **No schedule time** | `5` |
| Build error (no table) | Treat as separate bucket or score `100` so it surfaces |

**Trip score** = sum of segment points (or `max` + `0.25 * sum` if we want one catastrophic segment to dominate — decide in spike).

**Default sort:** score descending; tie-break by max implied speed descending, then flagged count, then earlier terminus departure.

### 5.2 Alternate sort modes (UI)

1. **Severity score** (default)  
2. **Highest implied speed** (worst single segment) — best for “physically absurd”  
3. **Most flagged segments** — closest to today’s route scan mental model  
4. **Worst slow-long padding** — lowest implied km/h among segments ≥1 km  

### 5.3 Focus filters

Apply **before** Top N cut:

- Tight only → trips with ≥1 tight flag (score from tight evidence only).
- Slow / padding only → long-slow + relative-slow.
- Any → all flags.
- Errors → build failures only.

---

## 6. Deduplicating “the same journey”

Many trips share a stop pattern / shape at different times. Users may want either:

| Mode | Behaviour |
|------|-----------|
| **Every timed trip** (default) | Top X distinct `trip_id`s — what a driver runs today |
| **Unique pattern** | Collapse by `(route_id, direction_id, shape_id)` or hash of stop_id sequence; keep the **worst-scoring** timed example (or the most common dep) |

v1: ship **every timed trip**; add pattern collapse as a checkbox in v1.1 if the list is dominated by clones.

---

## 7. Performance and caps

### 7.1 Why this is the main risk

Each trip: load stop_times slice, load shape, project stops, compute speeds, flag. Dublin Bus **network × one Sunday** can be thousands of trips. Route scan already warns / caps at **500** per route. Streamlit Cloud is CPU- and time-limited; free tier **sleeps**.

### 7.2 Caps (proposed defaults)

| Knob | Local / beefy host | Streamlit Cloud |
|------|--------------------|-----------------|
| Max trips per run | 5000 | 800–1500 |
| Max routes if “all” | unlimited | still trip-capped |
| Top N max | 100 | 50 |
| Default Top N | 20 | 20 |

Show a checkbox: **“Scan all routes (may take several minutes)”** with the cap in the caption.

### 7.3 Speed tactics (implementation order)

1. **Route allowlist first** — best UX for drivers; cheapest.  
2. Batch `load_stop_times_for_trip_ids` in chunks (already chunked CSV read).  
3. Cache shapes by `shape_id` within a run (many trips share shapes).  
4. Optional on-disk cache: `data/cache/problem_scores_{feed_hash}_{date}.sqlite` keyed by `trip_id` so re-ranking Top N is instant after one full scan.  
5. Precompute job (later): nightly cron writes Top 50 for “today/tomorrow” into a small JSON the app reads without scanning.

### 7.4 Incremental scan UX

- Progress bar: trips processed / total.  
- **Show running Top N** while scanning (heap of size N) so the page feels alive.  
- Cancel: Streamlit cannot truly cancel easily; document “close tab / rerun” or use a cooperative `st.session_state.cancel` checked each trip if practical.

---

## 8. Suggested code layout (future)

```
auditor/
  network_scan.py       # list trips for day ± routes; orchestrate scan
  problem_score.py      # score_trip(rows) -> ProblemScore
  route_scan.py         # keep; maybe share helpers with network_scan
app.py                  # sidebar + results UI
docs/
  top-problem-journeys-plan.md
```

Enrich scan output beyond `TripScanRow`:

```text
ProblemTripRow:
  # existing TripScanRow fields
  route_short_name
  score
  rank_components  # e.g. max_speed_kmh, tight_count, slow_long_count
  worst_segment_summary
```

---

## 9. Delivery phases

### Phase 0 — Sizing spike

- Count trips with shapes for a Sunday / weekday in current GTFS.  
- Time `scan_trips_for_flags` on 50 / 200 / 500 trips.  
- Decide Cloud cap and whether “all routes” is offered on Cloud at all.

### Phase 1 — Multi-route scan + Top N (allowlist)

- Input: date, routes list, Top N, direction, focus.  
- Score + table + open trip + CSV.  
- No “all routes” yet (or all routes only if under cap).

### Phase 2 — All-routes mode + progress + caps

- Honest warnings when truncated.  
- Shape cache within run.  
- Optional pattern dedupe checkbox.

### Phase 3 — Persistence / precompute (optional)

- Score cache keyed by feed identity (ETag / content hash from existing download meta).  
- Or external cron writing `data/cache/top_problems_{date}.json`.

### Phase 4 — Polish

- Excel export parity with route scan.  
- URL state for date / routes / Top N.  
- CHANGELOG + README; tune weights from real driver feedback.

---

## 10. Edge cases

| Case | Handling |
|------|----------|
| Route short name collisions / variants | Match `route_short_name` exact strip like today; allowlist is user-facing names |
| Trip without shape | Exclude from candidates (same as route scan) |
| Entire day is “slow urban” | Relative-slow already backs off when trip median &lt; 22 km/h; scoring should not over-promote those |
| Feed refresh mid-session | Results are for the loaded `data/current/`; show last-downloaded caption |
| Huge allowlist typo | “No matching routes” with the unmatched tokens listed |
| User expects realtime problems | Copy: “These are **timetable vs shape** issues, not live running” |

---

## 11. Testing

- Unit tests for `score_trip` with synthetic `SegmentRow`s (tight, slow-long, mixed).  
- Ranking stability: fixed fixture feed slice → stable Top N order.  
- Allowlist parsing: spaces, case, empty tokens.  
- Manual: Sunday vs weekday counts; one known bad trip appears when allowlisted.

---

## 12. Success criteria

- [ ] User picks **today** + optional routes + Top 20 and sees a ranked list without manually guessing trip times.  
- [ ] Opening a row matches a normal audit for that `trip_id`.  
- [ ] A single extreme tight segment outranks a trip with many mild relative-slow flags (under default score).  
- [ ] On Cloud, a bounded run completes with a clear cap message rather than hanging until timeout.  
- [ ] Schedule disclaimer remains visible: heuristics, not traffic law.

---

## 13. Open questions

1. Default on Cloud: **allowlist-only** vs capped all-routes?  
2. Should Top X include **build errors** in the same list or a second panel?  
3. Weights for tight vs slow-long — confirm with a few real “this is unfair” examples from drivers.  
4. Do we want a one-click **“Scan my routes”** preset stored in secrets/env (e.g. `DEFAULT_ROUTE_ALLOWLIST=39,140`)?  
5. Same Excel workbook as route scan vs separate `TopProblems_*.xlsx`?

---

## 14. Summary

**Top problem journeys** = calendar day + (optional) route filter → scan trips with the existing flag engine → **rank by severity** → show Top N → drill into the auditor you already have.

Ship allowlisted multi-route Top N first; treat full-network scan as a capped / cached mode so Streamlit Cloud stays usable.

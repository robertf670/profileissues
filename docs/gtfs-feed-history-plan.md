# Plan: Historic GTFS snapshots & profile change proof

**Status:** planning only — not implemented  
**Related product:** Profile Issues / Dublin Bus schedule auditor  
**Related plans:** [GTFS-RT actual times](gtfs-rt-actual-stop-times-plan.md) (static pins), [Top problem journeys](top-problem-journeys-plan.md)

---

## 1. Goal

### 1.1 Primary user story

> NTA publishes a new Dublin Bus GTFS. A route’s **profile** (shape and/or stop-to-stop times) changes in a way that looks worse for drivers — e.g. less time for the same road distance, or a shape tweak that changes implied speeds.  
> I want to open two feed versions — **before** and **after** — match the same logical journey (e.g. route 39, outbound, 17:32 on a comparable service day), and **prove what changed**: segment distances, allowed times, implied speeds, flags, and optionally the map polyline.

### 1.2 Secondary goals

- Keep an **append-only local archive** of GTFS zips whenever our download detects a real content change (ETag / hash), not only `data/current/`.
- Browse history: when each snapshot was fetched, remote Last-Modified / ETag, content hash, optional `feed_info` validity dates.
- Diff **one trip** first; later optional “what changed on route 39?” summaries.
- Survive app restarts when hosted on a **persistent volume** (history on ephemeral Streamlit Cloud is unreliable).
- Clear disclaimer: this is comparison of **published open data snapshots we retained**, not an official NTA change log.

### 1.3 Non-goals (v1)

- Guaranteeing history for dates **before we started archiving** (unless we later import third-party archives).
- Treating Transitland / other mirrors as the sole store (optional import later; access to old versions may be gated).
- Proving live AVL / “what the bus did” (RT plan).
- Automatic legal submission packs (export of facts is enough; lawyers/format later).

---

## 2. Why this is needed

| Fact | Implication |
|------|-------------|
| Public TFI URL serves the **latest** `GTFS_Dublin_Bus.zip` | Once replaced, the previous profile is gone from that URL |
| This app’s `download_gtfs` **deletes and recreates** `data/current/` | No history today |
| `trip_id` values are **not stable** across publishes (NTA has warned IDs are internal / generational) | Diffs must match **logical trips**, not raw IDs |
| “Check for update” already compares ETag / Last-Modified | Natural hook to archive **only when content changes** |

Without local (or trusted third-party) snapshots, “sneaky profile change” cannot be proven from the app after the fact.

---

## 3. Target UX

### 3.1 History browser (sidebar or settings)

- List snapshots: `fetched_at` (Europe/Dublin), `content_hash` short, ETag, remote Last-Modified, size, `feed_info` start/end if present.
- Actions: **Set as current** (extract into `data/current/` for normal audit), **Compare…**, **Delete** (optional, with confirm), **Export zip**.
- Badge on main app: which snapshot `data/current/` corresponds to (hash + date).

### 3.2 Compare two snapshots (core)

Inputs:

| Input | Notes |
|-------|--------|
| Snapshot A (baseline) | Older |
| Snapshot B (newer) | Often “current” |
| Same trip selectors as audit | Route, direction, terminus departure, service date |
| Service date policy | Same calendar date in both feeds, **or** “nearest equivalent service day” if one feed’s calendar dropped that day |

Outputs:

1. **Match status** — found in A / found in B / ambiguous / missing.  
2. **Trip identity card** — resolved `trip_id` (A vs B), `shape_id`, headsign, service_id.  
3. **Segment diff table** — aligned by stop sequence / stop_id chain (§5):

| From → To | Dist A | Dist B | Δ m | Time A | Time B | Δ s | Speed A | Speed B | Flags A | Flags B | Note |
|-----------|--------|--------|-----|--------|--------|-----|---------|---------|---------|---------|------|

4. **Summary strip** — e.g. “3 segments tighter by ≥10 km/h implied”, “shape length −420 m”, “2 new slow-long flags”.  
5. **Map (phase 2)** — shape A vs shape B overlays + stops.  
6. **Export** — Excel/CSV of the diff + provenance block (hashes, fetch times, URLs).

### 3.3 “What changed on this route?” (phase 3)

- Pick route + snapshots A/B + service date.  
- List logical trips where score/delta exceeds thresholds (reuse problem-score ideas).  
- Top changed journeys — sibling to Top problem journeys, but **cross-feed**.

---

## 4. Storage design

### 4.1 Layout

```
data/
  current/                          # active extract (existing)
  history/
    index.json                      # or SQLite catalogue
    snaps/
      20260407T153012Z_a1b2c3d4/
        meta.json                   # etag, last_modified, url, sha256, bytes, fetched_at
        GTFS_Dublin_Bus.zip         # immutable bytes as downloaded
        # optional: extracted/ for faster repeat compares (trade disk for CPU)
```

**Gitignore:** keep ignoring `data/` (already). Never commit zips.

### 4.2 `meta.json` (per snapshot)

```json
{
  "snapshot_id": "20260407T153012Z_a1b2c3d4",
  "fetched_at": "2026-04-07T15:30:12+00:00",
  "source_url": "https://www.transportforireland.ie/transitData/Data/GTFS_Dublin_Bus.zip",
  "etag": "...",
  "last_modified_header": "...",
  "sha256": "...",
  "bytes": 123456789,
  "feed_info": {
    "feed_start_date": "...",
    "feed_end_date": "...",
    "feed_version": "..."
  }
}
```

### 4.3 When to create a snapshot

Hook into download / check-update flow:

1. User clicks **Download / refresh GTFS** (or startup auto-refresh).  
2. Download zip to a temp file; compute **SHA-256**.  
3. If hash **equals** latest history entry (or current extract’s recorded hash) → skip new history row; still refresh `current/` if desired.  
4. If hash **differs** → write `data/history/snaps/.../GTFS_Dublin_Bus.zip` + `meta.json`, update index, then extract to `data/current/` as today.  
5. Always record the hash of whatever is in `current/` so the UI knows provenance.

Also: optional **manual “Save current into history”** if someone copied files in by hand.

### 4.4 Retention

| Policy | Default idea |
|--------|----------------|
| Keep all changed publishes | Ideal for proof; disk grows with publish frequency |
| Cap by count | e.g. last 60 distinct hashes |
| Cap by age | e.g. 18 months |
| Pin | User can **pin** snapshots so GC never deletes them |

Dublin Bus zips are large; document disk needs on the persistent host. Deduplicate by hash so identical re-downloads do not clone.

### 4.5 Hosting dependency

History is useless on hosts that wipe `data/` (typical free Streamlit Cloud). Document: **persistent volume required** for this feature (same as long-term RT archive). Local + VPS/PaaS with mounted `data/` are first-class.

---

## 5. Matching the same logical trip across feeds

Do **not** rely on `trip_id` equality.

### 5.1 Match key (v1)

Resolve independently in snapshot A and B with existing `match_trip` logic:

- `route_short_name`
- `direction_id`
- terminus first-stop `departure_time` (normalised)
- `service_date` → `service_id`s running that day
- shaped trips only

If exactly one trip in each → compare.  
If zero / many → show candidates (departure, headsign, trip_id) and let user pick each side.

### 5.2 Stronger fingerprint (v1.1)

When terminus time alone is ambiguous or drifts:

- Ordered `stop_id` sequence hash  
- Or `(shape_id` geometry hash + first/last stop + first dep)`  
- Headsign as soft signal only (already removed from main form; optional)

### 5.3 Service day caveats

- Same calendar date may be holiday vs not across feeds if calendars differ.  
- UI should show which `service_id`s matched in A vs B.  
- Optional: “use next weekday both feeds agree is a service day.”

---

## 6. Diff algorithms

### 6.1 Segment alignment

Build `SegmentRow` lists for both trips (existing `build_segment_table` + flags).

Align rows by:

1. Prefer **stop_id pair** `(from_stop_id, to_stop_id)` in sequence order.  
2. If stops added/removed → insert gap rows (“only in A” / “only in B”).  
3. Fallback: `stop_sequence` index alignment with a simple LCS on stop_id lists if patterns diverge heavily.

### 6.2 Metrics per aligned segment

- Δ distance (m)  
- Δ allowed time (s)  
- Δ implied speed (km/h)  
- Flag set symmetric difference  

### 6.3 Trip-level rollups

- Total shape length A vs B (or sum of segment distances)  
- Count segments with \|Δ speed\| ≥ threshold (e.g. 5 km/h)  
- Count new/removed tight / slow-long flags  
- Max implied speed A vs B  

### 6.4 Shape geometry diff (phase 2)

- Compare `shape_id` point sequences (Hausdorff / length / simple plotted overlay).  
- Call out large length change even when stop_times unchanged (“geometry-only change”).

---

## 7. Architecture

```
Download / refresh
       │
       ├─► sha256(zip)
       ├─► if new hash: archive under data/history/snaps/...
       └─► extract → data/current/  (+ record current_snapshot_id)

Compare UI
       │
       ├─► open zip or cached extract for A and B
       ├─► match_trip(A), match_trip(B)
       ├─► build_segment_table both
       └─► align + diff + export
```

Suggested modules:

```
auditor/
  feed_history.py       # index, archive_on_download, list/get/pin/gc
  feed_diff.py          # align segments, rollups
  trip_match.py         # reuse; maybe accept gtfs_dir explicitly (already does)
app.py                  # History + Compare panels
```

Change `download_gtfs` to call `feed_history.maybe_archive(zip_bytes, meta)` **before** wiping `current/`.

---

## 8. Configuration

| Variable | Purpose |
|----------|---------|
| `GTFS_HISTORY_DIR` | Default `data/history` |
| `GTFS_HISTORY_MAX_SNAPSHOTS` | e.g. `60` (unpinable GC) |
| `GTFS_HISTORY_MAX_AGE_DAYS` | optional |
| `GTFS_HISTORY_EXTRACT_CACHE` | `0/1` — keep extracted copies for faster compare |
| `HIDE_FEED_HISTORY` | optional UI hide for public demos |

---

## 9. Delivery phases

### Phase 0 — Spike

- Measure zip size and publish frequency (how often ETag changes).  
- Confirm hashing whole zip on download is acceptable (CPU/time).  
- Decide zip-only vs zip+extract cache.

### Phase 1 — Archive on download

- Immutable zip + meta + index.  
- UI list of snapshots + which one `current` is.  
- Dedup by sha256.  
- Retention cap + pin.  
- CHANGELOG / README: “history requires persistent disk.”

### Phase 2 — Trip compare

- Pick A/B + same audit inputs → segment diff table + summary + CSV/Excel.  
- Provenance block in exports (hashes, fetch times).

### Phase 3 — Map overlay + stop-sequence LCS

- Folium dual shape.  
- Better alignment when stops inserted/removed.

### Phase 4 — Route-level change scan

- “Top changed trips on route X between A and B.”  
- Optional link from Top problem journeys: “also worse than snapshot A.”

### Phase 5 (optional) — Import external archives

- Manual zip upload into history.  
- Document Transitland / other sources as **best-effort** backfill for pre-app dates (licensing + API tiers).

---

## 10. Edge cases & risks

| Risk | Mitigation |
|------|------------|
| Disk full | Caps, pin, clear messaging, compress optional |
| Ephemeral host loses history | README + in-app warning if history dir not writable / empty after restart |
| Same schedule, new zip packaging | Hash of zip may change with equivalent data — optional secondary hash of sorted critical files (`trips`,`stop_times`,`shapes`,`stops`) for “semantic equality” |
| Trip only exists in one feed | Explicit missing-side state; still show the surviving audit |
| User compares unrelated dates | Warn when service calendars disagree |
| Overclaiming “proof” | Wording: published snapshot comparison; timestamps + hashes in export |
| Privacy / redistribution | Follow TFI/NTA licence (CC BY etc.); storing for personal audit is fine; mass republishing of zips is a separate policy question |

---

## 11. Testing

- Unit: archive dedup (same bytes → one snap); GC respects pins.  
- Unit: segment alignment with inserted stop.  
- Fixture: two tiny fake GTFS dirs → known Δ distance / Δ time.  
- Manual: refresh when remote unchanged → no new snap; when changed → one new snap + current updates.

---

## 12. Success criteria

- [ ] After two real distinct publishes, history contains **two** snapshots without manual folder copying.  
- [ ] User can compare 39 / outbound / 17:32 across them and see segment-level Δ distance / time / speed.  
- [ ] Export includes hashes and fetch times sufficient to cite “what file we compared.”  
- [ ] Re-downloading the identical zip does not spam duplicate history rows.  
- [ ] On ephemeral hosting, UI warns that history will not persist.

---

## 13. Open questions

1. Default retention — count vs months vs “keep everything until disk warn”?  
2. Should **Check for update** alone archive when it detects newer (download in background) or only on explicit refresh?  
3. Semantic hash (critical files) vs zip hash for dedup?  
4. Is route-level change scan required for v1, or trip compare enough?  
5. Upload-old-zip UI in v1 for backfill?

---

## 14. Summary

To prove a profile change, **archive every distinct GTFS publish** under `data/history/`, keep `data/current/` as the working extract, and add a **compare view** that matches logical trips across two snapshots and diffs segment geometry, times, implied speeds, and flags.

This is schedule-history evidence — complementary to GTFS-RT actual-times and to Top problem journeys on a single feed — and it depends on **persistent storage** plus archiving starting **before** the sneaky change you care about (or importing an older zip).

# Proximity Enrichment Optimization Ideas

The `enrich_listings_with_proximity` function in `proximity.py` can be slow when the result set is large (e.g. ~90 seconds for many listings). Each listing x each rule triggers Google Directions or Places API calls sequentially, with a 50 ms throttle per call.

This document captures approaches to substantially reduce processing time.

---

## 1. Cap How Many Listings Are Enriched

Enrich only the first N listings (e.g. 20-30); leave the rest un-enriched or with `null` proximity.

- **Pros:** Minimal code change; predictable, bounded cost; large speedup on large result sets.
- **Cons:** Users see "unknown" for listings beyond the cap.
- **Implementation:** Add a `max_listings` parameter (default 30). Enrich the first N; for the rest, set `proximity[rule_key] = None`. Optionally surface a message: "Proximity computed for first 50 listings; refine or filter for more."

---

## 2. Use Google Distance Matrix API Instead of Directions API

The **Distance Matrix API** is designed for many origin-destination pairs in a single request (up to 25 origins x 25 destinations per request, one travel mode per request).

- **Pros:** Far fewer round trips; e.g. 25 listings x 1 destination in one call instead of 25 separate calls.
- **Cons:** Different API shape; need to refactor to batch-building and response parsing. One request per travel mode (driving, walking, transit).
- **Implementation:** Structure the matrix with **listings as rows (origins)** and **geocoded rule destinations as columns**, grouped by travel mode. Since the API accepts one mode per request, run one matrix per mode:
  - **Driving matrix:** rows = listings, columns = ref points for rules with `mode=driving`
  - **Walking matrix:** rows = listings, columns = ref points for rules with `mode=walk`
  - **Transit matrix:** rows = listings, columns = ref points for rules with `mode=transit`

  Batch listings into groups of 25 (rows) and destinations into groups of 25 (columns). Example: 100 listings with 3 rules across different modes -> 4 listing batches x 3 mode requests = 12 calls, instead of 300 Directions calls.

  Map response rows back to listings and columns back to rule destinations to populate `proximity[rule_key]`.

- **"Nearest transit station" rules:** The destination is per listing (resolved via Places API), so it cannot be expressed as a shared column. These still need per-listing Places + Directions calls, or an approximation via grid-cell caching (see #7).

---

## 3. Parallelize API Calls (ThreadPoolExecutor / asyncio)

Issue Directions and Places requests concurrently instead of sequentially.

- **Pros:** Can cut wall-clock time by 5-10x if not rate-limited.
- **Cons:** Higher risk of hitting Google rate limits; requires tuning concurrency.
- **Implementation:** Use `concurrent.futures.ThreadPoolExecutor` (e.g. 5-8 workers) to call `_get_directions` and `get_nearest_transit_station`. Keep a small throttle or use a semaphore to cap in-flight requests.

---

## 4. Filter First, Enrich Only the Filtered Set

Apply cheap filters (price, bedrooms, etc.) before enrichment, then enrich only the reduced set.

- **Pros:** Leverages existing flow; fewer listings to enrich.
- **Cons:** Depends on users filtering before proximity; doesn't help when they want proximity on a large initial set.
- **Implementation:** Update `flow_instructions` so the agent applies `filter_listings` (without proximity rules) before calling `enrich_listings_with_proximity`.

---

## 5. Haversine Pre-Filter for "Within X Minutes" Rules

Use straight-line (haversine) distance as a fast, approximate filter; call Directions only for listings that pass.

- **Pros:** No API calls for the filter; instant filtering.
- **Cons:** Walk/drive time estimates from haversine are rough; may need heuristics (e.g. ~5 km/h for walk, ~30 km/h for drive).
- **Implementation:** Add a haversine-based "within radius" check; run Directions only for listings that pass. Optionally show "~X km" from haversine until real Directions are fetched.

---

## 6. Reduce or Remove `time.sleep(0.05)` Throttle

The current 50 ms sleep per rule per listing adds up: 50 listings x 2 rules = 5 seconds.

- **Pros:** Trivial change; immediate reduction in total time.
- **Cons:** May increase rate-limit risk; might need adaptive throttling instead.
- **Implementation:** Try `0.01` or remove entirely; monitor for rate-limit errors and reintroduce a smaller delay if needed.

---

## 7. Grid-Cell Approximation (Coarse Spatial Reuse)

Group listings into ~1 km grid cells; compute Directions once per cell centroid per destination; reuse for all listings in that cell.

- **Pros:** Often 10-50x fewer API calls.
- **Cons:** Less accurate for listings far from the centroid; requires tuning grid size.
- **Implementation:** Map each listing to a grid key, e.g. `(round(lat, 2), round(lon, 2))`. Compute Directions once per `(grid_key, destination, mode)`; assign results to all listings in that cell.

---

## 8. Defer / Lazy Enrichment

Enrich only when the user narrows results or explicitly requests proximity for a subset.

- **Pros:** No upfront cost for large sets.
- **Cons:** Flow and UI changes; users may expect proximity in the main table.
- **Implementation:** Don't enrich in the initial search; add "Show proximity" for selected listings or when the user filters/shortlists.

---

## Suggested Implementation Order

| Priority | Approach              | Effort | Impact  |
|----------|-----------------------|--------|---------|
| 1        | Cap listings (e.g. 30)| Low    | High    |
| 2        | Reduce or remove sleep| Low    | Medium  |
| 3        | Parallelize           | Medium | High    |
| 4        | Distance Matrix       | Higher | Highest |

**Pragmatic combination:** Cap (1) + reduce sleep (6) + parallelize (3) gives strong gains with moderate risk. Add Distance Matrix (2) if scaling to hundreds of listings.

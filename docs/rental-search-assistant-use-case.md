# Rental Search Assistant — Use Case (High Level)

An agentic MCP-based use case for **natural-language rental search**: the user describes what they want in quantitative and qualitative terms; the agent clarifies, searches, verifies criteria (including proximity), shortlists, and submits viewing requests on the user’s behalf across different listing platforms.

---

## Overview

The agent:

- **Parses** natural language into structured search criteria (beds, sqft, rent, location, transit, drive time).
- **Asks clarification questions** when the request is ambiguous, and **must** ask for the user’s preferred days and times for viewings (these preferences are respected when submitting requests).
- **Searches** at least one rental search engine (via API or browser automation).
- **Verifies** criteria that the search engine doesn’t support (e.g. walk time to skytrain, drive time to downtown).
- **Returns a shortlist** and lets the user **approve a subset** for viewing.
- **Checks the user’s calendar** for available timeslots when preparing or submitting viewing requests.
- **Logs viewing requests** (pending or confirmed) per timeslot to avoid double booking.
- **Submits viewing request forms** on each listing’s platform (each platform may have a different form structure).

---

## Example User Search

> *"I am looking for a 2+ bedroom apartment no smaller than 800 sqft, in the range of 2500 to 3000 CAD monthly rent, in Vancouver metropolitan area, in max 5 min walk to a skytrain station, max 45 min drive to downtown."*

**Structured criteria (parsed by agent):**

| Criterion | Value |
|-----------|--------|
| Min bedrooms | 2 |
| Min sqft | 800 |
| Rent range | 2500–3000 CAD/month |
| Location | Vancouver metropolitan area |
| Walk to skytrain | ≤ 5 min |
| Drive to downtown | ≤ 45 min |

---

## 1. Intent and Clarification

The agent parses the user’s natural language into structured fields. When meaning is ambiguous, it asks before searching. The agent **must** ask the user for their **preferred days and times** for viewing bookings; this preference is then respected when submitting viewing requests (only slots within the user’s stated preference are used).

**Clarification examples:**

- **Preferred days and times (required):** *"Which days and times work best for viewings? For example: weekday evenings, weekend mornings, or specific dates and time windows."* The agent collects this (e.g. via multi-choice or free text) and stores it as *viewing preference* (e.g. “weekdays 6–8pm”, “Sat–Sun 10am–2pm”, or “Feb 15–20, any time 9am–5pm”). All later slot selection and form submissions use only times that fall within this preference.
- **Geography:** *"Vancouver metropolitan area — do you mean City of Vancouver only, or Metro Vancouver (Burnaby, New West, etc.)?"*
- **Downtown:** *"Which downtown should I use for drive time — Vancouver CBD or another area?"*
- **Transit:** *"Any skytrain line, or a specific line (Expo, Millennium, Canada Line)?"*
- **Search source:** *"Search on Realtor.ca, PadMapper, or both?"* (if multiple engines are supported)

**MCP:** Use an `ask_user(prompt, choices[])` tool (or multi-select) so the agent can ask these questions and continue with a single, clear set of criteria (including viewing preference).

---

## 2. Search (Engine API or Browser)

**Goal:** Run the search using whatever filters the engine supports and return a list of listings.

**Tool:** e.g. `rental_search(filters)` or `search_rentals(engine, min_bedrooms, min_sqft, rent_min, rent_max, location)`.

**Backend behaviour:**

- Prefer **rental APIs** (e.g. Realtor.ca, PadMapper) when available.
- If no API: the MCP server uses **browser automation** (e.g. Playwright/Puppeteer) to open the site, apply filters, and scrape the result list (URLs, addresses, price, beds, sqft, platform).

**Return shape:** List of listings, e.g.  
`{ id, title, url, address, price, bedrooms, sqft?, source/platform }`.

The agent calls this once (or once per engine) after clarification, then passes the list to the verification step.

---

## 3. Verification (Criteria Not in the Search Engine)

**Goal:** Keep only listings that also satisfy “max 5 min walk to skytrain” and “max 45 min drive to downtown,” which typical rental UIs don’t support.

**Tool options:**

**Option A — Single “proximity” tool**

- `check_proximity(address, checks)`
- `checks` is e.g.  
  `[{ "type": "walk_to_skytrain", "max_minutes": 5 }, { "type": "drive_to_downtown", "max_minutes": 45 }]`
- The server geocodes the address, then uses routing/transit APIs (e.g. OpenRouteService, Google Distance Matrix, or transit APIs) to compute walk time to nearest skytrain and drive time to downtown; returns pass/fail and actual times.

**Option B — Two tools**

- `transit_walk_time(address, destination_type, max_minutes)`  
- `driving_time(address, destination, max_minutes)`  
- The agent calls both per listing (or the server batches). “Downtown” and “nearest skytrain” can be config (e.g. downtown = Vancouver CBD; destination_type = `"skytrain_station"`).

**Data:** Geocoding (address → lat/lon) may be needed; it can live inside the MCP server or behind a small `geocode(address)` tool. Downtown and skytrain stations can be fixed config or resources.

**Agent behaviour:** For each search result (or in batches), call the proximity tool(s), then filter to listings that pass both constraints. The result is the **shortlist**.

---

## 4. Shortlist and User Approval

**Goal:** Present the shortlist and let the user choose which listings to request viewings for.

- **Shortlist:** The agent (or a tool) returns the filtered list with key fields and why they passed (e.g. “3 min walk to skytrain, 35 min drive to downtown”).
- **Approval:** Use `ask_user` with multi-select (e.g. “Which listings do you want to schedule viewings for? [Listing A] [B] [C] …”) or a dedicated `get_user_approval(listing_ids[])` that the client renders as checkboxes and returns the selected IDs/urls.

The agent, after building the shortlist, calls the approval tool and receives the subset of listings to submit viewing requests for.

---

## 5. Calendar Access (Availability & Viewing Log)

**Goal:** Use the user’s calendar to find available timeslots for viewings, and maintain a log of which timeslots already have a viewing request (pending or confirmed) so the agent does not double-book.

### 5.1 Check available timeslots

**Tool:** e.g. `get_available_timeslots(date_range)` or `check_calendar_availability(start_date, end_date)`.

- The MCP server connects to the user’s calendar (e.g. Google Calendar, Outlook) via OAuth or a configured calendar API.
- Returns a list of **free slots** in the given range (e.g. next 7 or 14 days), optionally with duration (e.g. 1-hour windows).
- The agent uses this together with the **user’s stated viewing preference** (days and times from clarification): only slots that are both free on the calendar and within the user’s preference are used. The agent can suggest “I can request viewings for Tuesday 2–4pm or Wednesday 10am–12pm” (within your preferred times) or pre-fill those times when submitting forms.

### 5.2 Log viewing requests (avoid double booking)

**Tool:** e.g. `log_viewing_request(listing_id_or_url, timeslot, status)` and `get_viewing_requests(date_range?)`.

- **`log_viewing_request(listing_id_or_url, timeslot, status)`**  
  - Records that a viewing request was submitted for a given listing at a given timeslot.  
  - `status`: e.g. `"pending"` (submitted, awaiting landlord confirmation) or `"confirmed"`.  
  - The server stores this in a small store (DB, file, or in-memory) keyed by timeslot (and optionally listing).

- **`get_viewing_requests(date_range?)`**  
  - Returns existing viewing requests in the log (listing, timeslot, status).  
  - If `date_range` is provided, filter to that range.  
  - The agent calls this before submitting new requests: if a slot is already used (pending or confirmed), it picks another slot from `get_available_timeslots` or asks the user.

**Agent behaviour:** Before submitting each viewing request, the agent calls `get_available_timeslots` and `get_viewing_requests`, restricts to slots that fall within the **user’s preferred days and times** (from clarification), excludes already-used slots, then chooses a free slot and call `log_viewing_request` when submitting (or immediately after a successful submit). That way the same slot is not used for two different listings, and only the user’s preferred windows are used.

---

## 6. Submitting Viewing Requests (Different Form Structures)

**Goal:** For each approved listing, open the listing URL and submit the platform’s “request viewing” / “contact” form with the user’s details.

**Challenge:** Each platform has a different form (fields, selectors, steps). The MCP server should abstract “submit a viewing request for this listing” in a platform-aware way.

**Options:**

**Option A — One generic tool, server-side adapters**

- `submit_viewing_request(listing_url, user_details)`
- The server infers platform from URL (e.g. realtor.ca, craigslist.org, padmapper.com).
- Internal registry of **platform adapters**: each adapter knows that platform’s form (selectors, field names, submit button).
- The server uses browser automation to navigate to `listing_url`, fill the form with `user_details` (name, email, phone, preferred times, message), submit, and return success/failure (and optionally a screenshot or confirmation text).

**Option B — Platform-specific tools**

- `submit_viewing_realtor_ca(listing_id_or_url, user_details)`  
- `submit_viewing_craigslist(listing_url, user_details)`  
- The agent infers platform from listing metadata and calls the right tool. More explicit and easier to extend per platform.

**Option C — Hybrid**

- `submit_viewing_request(listing_url, platform?, user_details)`
- If `platform` is omitted, the server infers from URL; otherwise it uses the given platform. Same adapter registry as in A.

**User details:** Name, email, phone, **preferred days and times for viewings** (from clarification), and optionally a standard message. These can be gathered in clarification and contact-details flow and then passed into each submit call, or stored in an MCP resource (e.g. `user_profile://contact`) that the server reads when submitting.

**Respecting preference:** When submitting, the agent **must** use only times that fall within the user’s stated preferred days and times. The `user_details` (or form pre-fill) should include those preferred times so the listing’s “request viewing” form is submitted with the user’s preferred slots (e.g. “Weekday evenings 6–8pm” or specific dates/times). The agent must not suggest or submit viewings outside the user’s stated preference.

**Agent behaviour:** For each approved listing, pick a slot within the user’s preference (and not already logged), call `submit_viewing_request(url, user_details)` with that time in `user_details`, then summarize which succeeded and which failed (and why, if the server returns it).

---

## 7. MCP Server Surface (Summary)

| Kind    | Name | Role |
|---------|------|------|
| Tool    | `ask_user(prompt, choices[])` | Clarification and approval (multi-choice / multi-select). |
| Tool    | `rental_search(filters)` | Search one or more rental engines (API or browser); return listing list. |
| Tool    | `check_proximity(address, checks)` | Verify walk to skytrain and drive to downtown (or similar). |
| Tool    | `get_available_timeslots(date_range)` | Check user’s calendar for free slots in the given range. |
| Tool    | `get_viewing_requests(date_range?)` | Return logged viewing requests (listing, timeslot, status) to avoid double booking. |
| Tool    | `log_viewing_request(listing_id_or_url, timeslot, status)` | Log a viewing request (pending or confirmed) for a timeslot. |
| Tool    | `submit_viewing_request(listing_url, user_details)` (and optionally `platform`) | Submit viewing/contact form (platform-specific adapters inside server). |
| Resource | `user_profile://contact` (optional) | Stored contact details for form submission. |
| Resource | `config://proximity` (optional) | Downtown definition, skytrain stations, API keys (server-side config). |

The shortlist can be pure agent output (no extra tool). Geocoding can be internal to the server or a separate `geocode` tool if the agent should reason about addresses.

---

## 8. Agent Flow (End-to-End)

1. **Parse** — From user NL, extract: beds, sqft, rent range, location, walk to skytrain ≤ 5 min, drive to downtown ≤ 45 min.
2. **Clarify** — If location, “downtown,” or transit is ambiguous, call `ask_user`; update criteria. **Must** ask for preferred days and times for viewings and store as *viewing preference*.
3. **Search** — Call `rental_search(filters)`; get raw listing list.
4. **Verify** — For each listing (or batch): get address → `check_proximity(address, checks)` → keep only passing listings → **shortlist**.
5. **Present** — Return shortlist to user (and optionally “which do you want to request viewings for?”).
6. **Approve** — `ask_user` multi-select (or `get_user_approval`) → user selects subset.
7. **Calendar & slots** — Call `get_available_timeslots(date_range)` and `get_viewing_requests(date_range)`; derive free slots that are (a) available on the calendar, (b) within the user’s **preferred days and times**, and (c) not already used for a viewing. Optionally ask user to confirm from these slots.
8. **Submit** — For each approved listing: pick a slot from the free slots (respecting preferred days/times), call `submit_viewing_request(listing_url, user_details)` with that time in `user_details`; on success call `log_viewing_request(listing_url, timeslot, "pending")` so the slot is not reused; collect success/failure.
9. **Confirm** — Reply with: “Viewing requests submitted for [A, B] at [times]; failed for [C] (reason).”

---

## 9. Design Notes

- **APIs vs browser:** Use rental and maps/transit APIs where possible (and allowed); use browser automation only where necessary (and document ToS/legal caveats).
- **Proximity:** “Downtown” and “skytrain stations” can be configured once (e.g. GeoJSON or list of coordinates) so the server can compute walk/drive times consistently.
- **Forms:** Platform-specific adapters (one per site) are more robust than a single generic form-filler; start with 1–2 platforms and add more.
- **Rate limiting / ethics:** Throttle search and form submission; consider captchas and human-in-the-loop for sensitive actions.
- **Calendar:** Use OAuth or a service account for calendar read access; keep the viewing log in a small store (e.g. SQLite or a JSON file) so it persists across sessions and avoids double booking even when the user runs multiple searches.

---

## Next Steps (Ideas)

- Define exact tool schemas (parameters, return types) for `rental_search`, `check_proximity`, `get_available_timeslots`, `get_viewing_requests`, `log_viewing_request`, `submit_viewing_request`.
- Choose and document specific free/paid APIs for rental search, geocoding, and routing/transit.
- Design the platform-adapter registry and form-selector strategy for each supported listing site.
- Specify the `ask_user` / `get_user_approval` contract and how the client renders multi-select.

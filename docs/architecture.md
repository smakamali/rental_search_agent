# Architecture

This document describes how the Property Search Assistant is structured. Product scope and surfaces are in [overview.md](overview.md). Tool contracts are summarized in [tools.md](tools.md).

## Layers

```text
┌─────────────────────────────────────────────────────────────┐
│  Presentation                                                │
│  streamlit_app · streamlit_results · streamlit_analysis      │
│  client (CLI) · MCP hosts                                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  Agent orchestration                                         │
│  client.run_agent_step_events · agent.flow_instructions      │
│  AGENT_TOOLS → run_tool() (in-process for UI/CLI)            │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  Domain                                                      │
│  adapter · preference_apply · filtering · proximity*         │
│  match_scoring · search_regions · preference_* · summarizer  │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  Backends & external APIs                                    │
│  Apify → REALTOR.CA · OpenRouter/OpenAI · Google Maps/Cal    │
└─────────────────────────────────────────────────────────────┘
```

**UI and CLI do not speak MCP over the network.** They call the same Python functions the MCP server wraps. MCP is for external agent hosts only.

## End-to-end data flow

### Chat-driven search

1. User message is appended to the conversation. The system prompt includes today’s date, `agent.flow_instructions()`, and a block of stored Search Preferences when present.
2. The LLM may call tools in rounds via `run_agent_step_events` (OpenAI-compatible chat completions with tool calling).
3. Geography: metro name → `expand_search_region` → `ask_user` multi-select → `location: [cities]`.
4. `rental_search` → capability checks (guest scrape / multi-city) → `adapter.search`:
   - one city → `ApifyRealtorCaBackend.search`
   - many cities → thread pool (`APIFY_MAX_CONCURRENT`) → merge/dedupe by listing id
5. After a successful scrape with listings, **`apply_search_preferences`** runs in code (not as an LLM-chosen chain):
   - structural `filter_listings`
   - parse / geocode / enrich proximity when proximity text is set
   - `score_listings_by_preferences` → sort by `match_score`
   - assign 1-based `rank` for table, map, and “listing N” references
6. Synthetic tool messages inject the applied listings so the model sees the ranked set.
7. `summarize_listings` → model writes a bullet summary; Streamlit binds `display_list` / related session state.
8. Refine: reductive filters operate on the current display set; relaxing within scrape bounds restores from the enriched master; criteria outside the last scrape require a new `rental_search`.

### Sidebar Search

`prepare_sidebar_search` (via preference apply helpers): if location / listing type / structural scrape keys changed → new scrape + pipeline; otherwise **re-rank only** on the existing master list.

### Analyze listing

UI triggers `listing_analysis` with stored prefs, chat criteria, and optional proximity rules; optional chat summary for conversation context.

## Listing state

| Concept | Meaning |
|---------|---------|
| **Search master** | Raw listings from the last successful `rental_search` (recoverable corpus). |
| **Master / display** | Preference-applied set after filter/proximity/score; what the table and map show. |
| **`rank`** | Explicit 1-based identity on each listing after preference apply / relevant tool results. Do not recompute from display order alone. |

Conversation tool-result messages are the durable source for reconstructing listing corpora in the CLI/agent loop; Streamlit also mirrors lists in `st.session_state`.

## Key modules (`src/rental_search_agent/`)

| Module | Responsibility |
|--------|----------------|
| `agent.py` | `flow_instructions()`, `AgentState`, approval helpers, `TOOL_STATUS_LABELS` |
| `client.py` | Tool schemas, `run_tool`, `AGENT_TOOLS`, agent loop, auto-apply after search, CLI |
| `server.py` | FastMCP stdio server wrapping domain operations |
| `streamlit_app.py` | Chat UI, auth sidebar, Search Preferences, session wiring |
| `streamlit_results.py` | Results table / map presentation |
| `streamlit_analysis.py` / `analysis_view.py` / `display_format.py` | Analyze UI and formatting |
| `adapter.py` | Multi-city fan-out, merge, dedupe |
| `backends/apify_realtor_ca.py` | Apify actor runs, retries, map dataset → `Listing` |
| `backends/base.py` | `SearchBackend` protocol; `get_search_backend()` (`SEARCH_MARKET=ca`) |
| `search_regions.py` | Canadian metro catalogs → city labels / `search_location` |
| `preference_apply.py` | Deterministic post-search pipeline; sidebar scrape vs re-rank |
| `preference_resolution.py` / `preference_store.py` / `preference_criteria.py` | Merge chat over stored prefs; JSON/SQLite persistence; scoring primitives |
| `filtering.py` | In-memory structural + proximity filter/sort |
| `proximity_parser.py` / `geocoding.py` / `proximity.py` | Rules, geocode, Distance Matrix / Directions / Places enrich |
| `match_scoring.py` / `semantic_scoring.py` / `scoring_config.py` | Weighted match score + embeddings |
| `listing_analysis.py` / `chat_summary.py` | Per-listing analysis; conversation context for Analyze |
| `summarizer.py` | Aggregate stats for chat summaries |
| `models.py` | Pydantic models (`RentalSearchFilters`, `Listing`, proximity, calendar, etc.) |
| `api_config.py` | Provider keys, `LLM_MODEL` / `EMBEDDING_MODEL`, clients |
| `session_runtime.py` / `auth_principal.py` / `auth_allowlist.py` / `capability_policy.py` | Principal, guest caps, allowlist |
| `calendar_service.py` / `viewing_plan.py` | Google Calendar + geo-clustered viewing plans (MCP / disabled in chat) |
| `logging_config.py` | Levels, optional file log, run ids, `log_stage` |

## Design choices

1. **LLM orchestrates; code owns cost** — After `rental_search`, preference apply runs filter → proximity → score in-process so Maps and ranking are not left to fragile multi-step tool guessing.
2. **Master vs display** — Scrape is recoverable; reductive filters use the display set; relaxing within scrape bounds restores from master with enrichment overlay.
3. **In-process tools for UI/CLI** — Same functions as MCP; no mandatory MCP hop for Streamlit/CLI.
4. **Pluggable search backend** — `SearchBackend` + `SEARCH_MARKET`; only Canada Apify today.
5. **Chat scope narrowed** — Booking/calendar tools remain implemented for MCP and future UI, but are stripped from `AGENT_TOOLS` and forbidden in `flow_instructions`.
6. **Guest vs signed-in policy** — Caps on scrapes, multi-city, and proximity rule count; scrape attempts consume guest credits.
7. **Multi-metric ranking** — Weighted structural + proximity + amenity + semantic; missing components are renormalized out (not scored as zero). Coverage is checklist-only (not part of overall `match_score`).
8. **Den ≠ bedroom** — Source strings like `"2 + 1"` parse so den does not inflate bedroom filters; qualitative/scoring handle dens.
9. **URL hygiene** — Listing/photo URL host allowlists for Realtor.ca when mapping scraped data.

## External integrations

| Service | Use |
|---------|-----|
| **Apify** + actor `igolaizola/realtor-canada-scraper-ppe` | Scrape REALTOR.CA `for_rent` / `for_sale` |
| **OpenRouter or OpenAI** | Chat agent + embeddings for semantic score |
| **Google Maps** | Geocoding, Distance Matrix / Directions, Places (transit) |
| **Google Calendar** | Optional slot discovery + events (MCP tools; not in chat agent) |
| **SQLite / JSON** | Signed-in prefs DB vs local/dev JSON prefs |

There is no primary SQL listing database — results live in session memory and conversation tool history.

## Related

- [Overview](overview.md)
- [Tools](tools.md)
- [Configuration](configuration.md)

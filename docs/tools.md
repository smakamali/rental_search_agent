# Tools

Domain operations are implemented as Python functions and exposed in two ways:

- **UI / CLI agent** — OpenAI-style tool schemas in `client.py` (`AGENT_TOOLS`). Calendar and viewing tools are registered in the full `TOOLS` list but excluded via `CHAT_DISABLED_TOOL_NAMES`.
- **MCP server** — Same domain wrapped as FastMCP tools in `server.py` (stdio). Includes calendar and viewing tools.

Authoritative behaviour for the chat product is `agent.flow_instructions()` plus the auto pipeline in `preference_apply.apply_search_preferences`.

## Chat-active tools

| Tool | Purpose |
|------|---------|
| `ask_user` | Clarify or confirm — single answer or multi-select (CLI prompt / Streamlit form). |
| `expand_search_region` | Metro/region name → city `label` + `search_location` list. |
| `rental_search` | Apify scrape (`for_rent` / `for_sale`). `location` is one city or a list; parallel multi-city merge. |
| `filter_listings` | Narrow and/or sort the current set; optional `proximity_rules`. Relaxing within scrape bounds can restore from master. |
| `summarize_listings` | Aggregate stats for the bullet summary. |
| `parse_proximity_preferences` | Free text → `{ rules: [{ location, mode, max_minutes }, ...] }`. |
| `geocode_location` | Single location → lat/lon (Google Geocoding). |
| `geocode_proximity_references` | Geocode rule destinations (skips “nearest transit station”). |
| `enrich_listings_with_proximity` | Attach per-rule distance/duration (Distance Matrix / Directions / Places). |
| `score_listings_by_preferences` | Multi-metric `match_score`; sort best-first. |
| `analyze_listing_preferences` | Single-listing match/gaps (UI Analyze; also callable as a tool). |

### Automatic preference pipeline (not an LLM tool choice)

After a successful `rental_search` with listings, the client/UI runs **`apply_search_preferences`** in process. That applies structural filter → proximity (when set) → scoring and assigns `rank`. The chat agent is instructed **not** to re-run filter / proximity / score for that initial search; those tools remain available for later explicit chat refinements.

## MCP-only / chat-disabled tools

Present on the MCP server and in `TOOLS`, but **not** offered to the Streamlit/CLI chat agent:

| Tool | Purpose |
|------|---------|
| `simulate_viewing_request` | Simulated request summary + mailto-style `contact_url` (no real form POST). |
| `calendar_list_events` | List Google Calendar events in a range. |
| `calendar_get_available_slots` | FreeBusy-style slots within preferred viewing times. |
| `calendar_create_event` / `calendar_update_event` / `calendar_delete_event` | Calendar CRUD. |
| `draft_viewing_plan` | Assign slots to listings; cluster nearby listings (~geo). |
| `modify_viewing_plan` | Add/remove/update plan entries. |

If a user asks to book or request a viewing in chat, the agent should refuse and offer to keep refining search results.

## Search filters (`rental_search`)

Core fields (Pydantic `RentalSearchFilters`):

| Field | Notes |
|-------|--------|
| `min_bedrooms` | Required |
| `location` | Required — city string or list of cities; **not** a metro name |
| `max_bedrooms`, `min_bathrooms`, `max_bathrooms`, `min_sqft`, `max_sqft` | Optional |
| `price_min`, `price_max` | CAD/month for rent; list price CAD for sale |
| `listing_type` | `for_rent` (default) or `for_sale` |

Response includes `listings`, `total_count`, `searched_locations`, and `failed_locations` (partial multi-city failures).

## Filter / sort (`filter_listings`)

Structural criteria: bedrooms, bathrooms, sqft, price, `house_categories`.  
Sort keys include: `price`, `bedrooms`, `bathrooms`, `sqft`, `address`, `id`, `title`, `match_score`, `semantic_score`, `proximity`, `listing_age_hours`.

Proximity rules use **AND** semantics. Listings without coordinates or with unknown proximity for a rule are kept (shown as distance unknown).

## Match scoring

Weighted components (env-tunable; renormalized over components that are present):

- Structural
- Proximity
- Amenity
- Semantic (embeddings)

Coverage is a checklist only — it is **not** part of overall `match_score`. See [configuration.md](configuration.md) for weight env vars.

## Related

- [Overview](overview.md)
- [Architecture](architecture.md)
- [Configuration](configuration.md)

# Property Search Assistant — Overview

Canadian property search assistant for **rent or sale** listings on [REALTOR.CA](https://www.realtor.ca/), driven by natural language (chat) or structured Search Preferences (Streamlit sidebar). Listings are scraped via an [Apify](https://apify.com/) actor; results are filtered, enriched with commute times, ranked, and shown in chat plus a table and map.

Package name: `rental-search-agent` (Python ≥ 3.10).

## What it does today

| Capability | Description |
|------------|-------------|
| **Natural-language search** | LLM agent parses beds, baths, size, budget, location, listing type (`for_rent` / `for_sale`). |
| **Metro / multi-city search** | Named metros expand to municipalities; user multi-selects cities; one scrape fans out in parallel and merges by listing id. |
| **Sidebar Search Preferences** | Location, listing type, structural prefs, proximity text, qualitative prefs; **Search** scrapes or re-ranks without chat. |
| **Preference pipeline** | After each successful scrape: structural filter → proximity enrich (if set) → multi-metric `match_score` ranking. Deterministic code, not an LLM tool chain. |
| **Refine in chat** | Narrow/sort/relax within the last scrape; out-of-scrape bounds trigger a new scrape. |
| **Proximity** | Free-text rules (e.g. “max 30 min drive to downtown”) via Google Geocoding, Distance Matrix / Directions, and Places. |
| **Analyze listing** | Per-listing match/gaps card using stored + chat criteria. |
| **Auth (Streamlit)** | Guests: capped scrapes / multi-city / proximity rules. Google sign-in: unlimited search, durable prefs (SQLite). |

## What it does not do (current product)

| Out of scope in chat / UI agent | Notes |
|----------------------------------|--------|
| Booking viewings / calendar scheduling | Calendar and viewing-plan tools still exist on the **MCP** surface and in the full tool registry, but are **excluded from the chat agent** (`CHAT_DISABLED_TOOL_NAMES`). |
| Real REALTOR.CA form submission | Only `simulate_viewing_request` (mailto-style summary) exists on MCP; not offered in chat. |
| US markets / other listing sites | `SEARCH_MARKET=ca` only; backend is pluggable for a future US seam. |
| Licensed CREA DDF feed | Scraping via Apify only. |

## Surfaces

| Entry point | Command | Role |
|-------------|---------|------|
| Streamlit UI | `rental-search-ui` | Primary product UI: chat, sidebar prefs, results table/map, Analyze. Tools run **in-process** (no MCP subprocess). |
| CLI client | `rental-search-client` | Same agent loop in the terminal. |
| MCP server | `rental-search-mcp` | Stdio FastMCP for external hosts (e.g. Cursor). Exposes the full tool set including calendar/viewing. |

## Typical chat flow

1. Parse criteria (merge with stored Search Preferences; chat overrides stored).
2. If a metro was named → `expand_search_region` → `ask_user` city multi-select.
3. `rental_search` once (single city or city list).
4. Automatic preference pipeline (filter → proximity → score).
5. `summarize_listings` + bullet summary; UI binds ranked table/map.
6. Optional refine (`filter_listings` / new search) → confirm with `ask_user`.

## Docs map

| Doc | Contents |
|-----|----------|
| [Docs index](README.md) | This folder’s table of contents |
| [Architecture](architecture.md) | Layers, data flow, modules, design choices |
| [Tools](tools.md) | Chat-active vs MCP-only tools |
| [Configuration](configuration.md) | Environment variables, auth, preferences storage |

Setup and run commands live in the root [README](../README.md).

# Property Search Assistant

Canadian property search assistant (rent **or** sale) for [REALTOR.CA](https://www.realtor.ca/) listings. Describe what you want in chat, or set Search Preferences in the Streamlit sidebar; the agent scrapes via **Apify**, then filters, enriches commute times, and ranks results for a table and map.

**Chat scope:** search and refine only. Calendar / viewing-booking tools exist on the MCP server but are disabled in the UI and CLI agent.

**LLM:** [OpenRouter](https://openrouter.ai) by default (one key, many models), or direct OpenAI.

Docs: [Overview](docs/overview.md) · [Architecture](docs/architecture.md) · [Tools](docs/tools.md) · [Configuration](docs/configuration.md)

## Setup

### Environment

```bash
conda create -n realtor_agent python=3.10
conda activate realtor_agent
pip install -r requirements.txt
pip install -e .
```

### Keys

Copy [`.env.example`](.env.example) to `.env`. Minimum for search:

| Variable | Role |
|----------|------|
| `OPENROUTER_API_KEY` or `OPENAI_API_KEY` | Chat agent (+ embeddings for ranking) |
| `APIFY_TOKEN` | REALTOR.CA scrape via Apify |
| `GOOGLE_MAPS_API_KEY` | Optional but required for proximity / commute prefs |

Full variable list: [docs/configuration.md](docs/configuration.md).

## Running

### Streamlit UI (primary)

```bash
rental-search-ui
# or: streamlit run src/rental_search_agent/streamlit_app.py
```

Includes chat, Search Preferences sidebar, ranked table/map, and Analyze. Guests get capped scrapes (`ANON_MAX_SEARCHES`). Sign in with Google for unlimited search, multi-city metro picks, and durable prefs. Without OIDC secrets, the app uses a local **dev** principal by default (`ALLOW_DEV_PRINCIPAL=true`); on shared hosts set `ALLOW_DEV_PRINCIPAL=false`.

Google sign-in: copy [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example) to `.streamlit/secrets.toml` and configure a Google OAuth Web client. Optional closed beta: `AUTH_ALLOWLIST_ENABLED` / `AUTH_ALLOWLIST`.

### CLI client

```bash
rental-search-client
# or: python -m rental_search_agent.client
```

Same agent loop in the terminal; `ask_user` prompts on stdin.

### MCP server (stdio)

For Cursor, Claude, or other MCP hosts (full tool set including calendar/viewing):

```bash
rental-search-mcp
# or: python -m rental_search_agent.server
```

## Features (short)

- **Metro search** — expand region → multi-select cities → parallel scrape + dedupe
- **Proximity** — free-text commute rules via Google Maps APIs (AND semantics; unknown distance kept)
- **Preference pipeline** — after scrape: structural filter → proximity → weighted `match_score`
- **Sidebar Search** — scrape or re-rank without chat when prefs change

## Google Calendar (MCP only)

Calendar tools need OAuth credentials (Desktop app) under `.rental_search_agent/` (or paths set in env). First use opens a browser; token is saved locally. Without credentials, calendar tools error. These tools are **not** available in the Streamlit/CLI chat agent.

## Testing

```bash
pip install -e ".[dev]"
pytest
pytest --cov=rental_search_agent --cov-report=term-missing
```

## Backend

- **In scope:** Canada REALTOR.CA via Apify actor `igolaizola/realtor-canada-scraper-ppe` (`for_rent`, `for_sale`). Pluggable seam for a future US market (`SEARCH_MARKET`).
- **Out of scope:** US actors, `for_sale_or_rent` / sold modes, licensed CREA DDF, real viewing-form automation in chat.

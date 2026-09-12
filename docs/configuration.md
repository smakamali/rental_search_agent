# Configuration

Copy [`.env.example`](../.env.example) to `.env` and fill in keys. Do not commit `.env`. Streamlit Google OIDC uses [`.streamlit/secrets.toml.example`](../.streamlit/secrets.toml.example) → `.streamlit/secrets.toml`.

## LLM

| Variable | Description |
|----------|-------------|
| `API_PROVIDER` | `openrouter` (default) or `openai` |
| `OPENROUTER_API_KEY` | Recommended for chat + embeddings via OpenRouter |
| `OPENAI_API_KEY` | Direct OpenAI when not using OpenRouter |
| `LLM_MODEL` | Chat model id (OpenRouter: `provider/model`; OpenAI: short name) |
| `EMBEDDING_MODEL` | Embedding model for semantic scoring |
| Legacy | `OPENROUTER_MODEL`, `OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL` — overridden when `LLM_MODEL` / `EMBEDDING_MODEL` are set |

## Search (Apify)

| Variable | Description |
|----------|-------------|
| `APIFY_TOKEN` | **Required** for property search |
| `APIFY_ACTOR_ID` | Default `igolaizola/realtor-canada-scraper-ppe` |
| `APIFY_MAX_ITEMS` | Max listings per search (default `100`) |
| `APIFY_MAX_CONCURRENT` | Max concurrent multi-city actor runs (default `5`) |
| `APIFY_FETCH_DETAILS` | Fetch listing descriptions / `PublicRemarks` (default `true`; extra cost/time) |
| `APIFY_MAX_RETRIES` | Extra attempts after transient failure (default `2` → 3 tries total) |
| `APIFY_RETRY_BASE_SECONDS` | Exponential backoff base (default `1.0`) |
| `SEARCH_MARKET` | Only `ca` is implemented |

## Maps, calendar, locale

| Variable | Description |
|----------|-------------|
| `GOOGLE_MAPS_API_KEY` | Required for proximity: enable **Geocoding**, **Directions**, **Distance Matrix**, and **Places** |
| `GOOGLE_CALENDAR_CREDENTIALS_PATH` | OAuth client JSON (default under `.rental_search_agent/`) — MCP calendar tools |
| `GOOGLE_CALENDAR_TOKEN_PATH` | Saved OAuth token path |
| `TIMEZONE` | Default `America/Vancouver` |

## Auth and preferences (Streamlit)

| Variable | Description |
|----------|-------------|
| `PREFS_DB_PATH` | SQLite for signed-in users (default `~/.rental_search_agent/preferences.db`) |
| `AUTH_ALLOWLIST_ENABLED` | When `true`, only allowlisted emails/domains get full accounts |
| `AUTH_ALLOWLIST` | Comma-separated emails and/or `@domains` |
| `ALLOW_DEV_PRINCIPAL` | Default `true`: missing OIDC secrets → local **dev** principal with JSON prefs. Set `false` on shared hosts so visitors are capped **guests**. |
| `ANON_MAX_SEARCHES` | Guest Apify scrapes per browser session (default `3`) |
| `ANON_MAX_PROXIMITY_RULES` | Max parsed proximity rules for guests (default `1`) |

**Accounts behaviour**

- **Guest** — limited scrapes; multi-city metro pick and higher proximity rule counts may be blocked (`sign_in_required` errors). Filtering/sorting an existing result set does not burn a scrape credit.
- **Signed in (Google OIDC)** — unlimited search, multi-city, durable prefs in SQLite.
- **Dev** — when secrets are missing and `ALLOW_DEV_PRINCIPAL=true`, local full access using `~/.rental_search_agent/preferences.json` (shared with CLI).

## Match scoring

Weights are non-negative and renormalized over present components. Coverage is checklist-only (not part of overall `match_score`).

| Variable | Default (conceptual) |
|----------|----------------------|
| `SCORE_WEIGHT_STRUCTURAL` | `0.35` |
| `SCORE_WEIGHT_PROXIMITY` | `0.25` |
| `SCORE_WEIGHT_AMENITY` | `0.20` |
| `SCORE_WEIGHT_SEMANTIC` | `0.20` |
| `SCORE_PARALLEL_WORKERS` | `4` |
| `SCORE_PARALLEL_MIN_LISTINGS` | `8` |

## Logging

| Variable | Description |
|----------|-------------|
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (default `INFO`) |
| `LOG_FILE` | Optional file path; relative paths resolve from project root. Console logging is always on (useful when Streamlit captures stderr). |

## Preferences storage summary

| Principal | Storage |
|-----------|---------|
| Guest / anonymous | Session-scoped behaviour; scrape counters in session |
| Dev / CLI (no OIDC) | `~/.rental_search_agent/preferences.json` |
| Signed-in Google user | SQLite at `PREFS_DB_PATH` |

## Related

- [Overview](overview.md)
- [Architecture](architecture.md)
- [Tools](tools.md)
- Root [README](../README.md)

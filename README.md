# Rental Search Assistant MVP

Chat-based rental search assistant: natural-language search, shortlist, viewing plan drafting (with Google Calendar), and simulated viewing requests. Uses REALTOR.CA via **pyRealtor** only; the Apify backend is **not** used in this MVP.

**LLM:** The client uses [OpenRouter](https://openrouter.ai) by default (one API key, 400+ models). You can still use direct OpenAI via `OPENAI_API_KEY`.

## Setup

### Conda environment

Create and activate the project environment:

```bash
conda create -n realtor_agent python=3.10
conda activate realtor_agent
```

Install dependencies and the package in editable mode:

```bash
pip install -r requirements.txt
pip install -e .
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | **Recommended** for the client. [OpenRouter](https://openrouter.ai) API key — one key for 400+ models (OpenAI, Anthropic, etc.). |
| `OPENROUTER_MODEL` | Optional. OpenRouter model ID (default: `openai/gpt-4o-mini`). Examples: `anthropic/claude-3.5-sonnet`, `google/gemini-pro`. |
| `OPENAI_API_KEY` | Alternative to OpenRouter. Direct OpenAI API key (used if `OPENROUTER_API_KEY` is not set). |
| `OPENAI_MODEL` | Optional when using OpenAI. Model name (default: `gpt-4o-mini`). |
| `USE_PROXY` | Optional. Set to `1`, `true`, or `yes` to enable proxy for pyRealtor (e.g. if REALTOR.CA rate-limits). |
| `GOOGLE_CALENDAR_CREDENTIALS_PATH` | Optional. Path to Google OAuth credentials JSON (default: `.rental_search_agent/credentials.json`). Required for calendar tools. |
| `GOOGLE_CALENDAR_TOKEN_PATH` | Optional. Path to store OAuth token (default: `.rental_search_agent/token.json`). |
| `TIMEZONE` | Optional. Timezone for calendar and date display (default: `America/Vancouver`). |

## Running

### MCP server (stdio)

For use by Cursor, Claude, or other MCP clients:

```bash
rental-search-mcp
```

Or:

```bash
python -m rental_search_agent.server
```

The server exposes eleven tools: `ask_user`, `rental_search`, `filter_listings`, `summarize_listings`, `simulate_viewing_request`, `calendar_list_events`, `calendar_get_available_slots`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`, `draft_viewing_plan`. It uses stdio by default.

### Chat client (CLI)

Runs the agent loop with a CLI: you type your search, the LLM calls tools; when it calls `ask_user`, you are prompted for answers or multi-select in the terminal.

```bash
set OPENROUTER_API_KEY=your-openrouter-key
rental-search-client
```

Or with OpenAI directly: `set OPENAI_API_KEY=your-key` (then `OPENROUTER_API_KEY` is ignored).

Or:

```bash
python -m rental_search_agent.client
```

Use `OPENROUTER_MODEL` to switch models (e.g. `anthropic/claude-3.5-sonnet`); or `OPENAI_MODEL` when using OpenAI directly.

### Streamlit UI

Web chat interface using the same agent and tools. Displays search results in a table and on a map (when coordinates are available). Set the same environment variables as the CLI (e.g. `OPENROUTER_API_KEY` or `OPENAI_API_KEY` in `.env` or your environment), then run:

```bash
rental-search-ui
```

Or:

```bash
streamlit run src/rental_search_agent/streamlit_app.py
```

The CLI remains available as `rental-search-client` or `python -m rental_search_agent.client` for terminal use.

## Google Calendar (optional)

Calendar tools (`calendar_get_available_slots`, `calendar_create_event`, etc.) use the Google Calendar API. To enable:

1. Create a project in [Google Cloud Console](https://console.cloud.google.com) and enable the Calendar API.
2. Download OAuth client credentials (Desktop app) as `credentials.json`.
3. Place it in `.rental_search_agent/` (or set `GOOGLE_CALENDAR_CREDENTIALS_PATH`).
4. On first use, a browser will open for OAuth; the token is saved at `.rental_search_agent/token.json`.

Without credentials, calendar tools return an error; the agent can fall back to a simulated-only flow (no events created).

## Testing

With the `realtor_agent` conda env active:

```bash
pip install -e ".[dev]"
pytest
pytest --cov=rental_search_agent --cov-report=term-missing
```

## Backend

- **In scope:** Single backend **pyRealtor** (REALTOR.CA, Canada/Vancouver). No other backends.
- **Out of scope:** Apify and the “Realtor.ca Property Search Scraper” actor are **not** implemented or used in this MVP.

## Docs

- [Technical spec](docs/rental-search-assistant-mvp-technical-spec.md)
- [MVP overview](docs/rental-search-assistant-mvp.md)

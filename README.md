# Rental Search Assistant MVP

Chat-based rental search assistant: natural-language search, shortlist, and simulated viewing requests. Uses REALTOR.CA via **pyRealtor** only; the Apify backend is **not** used in this MVP.

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
| `OPENAI_API_KEY` | **Required** for the client. API key for the LLM (OpenAI or compatible). |
| `OPENAI_MODEL` | Optional. Model name (default: `gpt-4o-mini`). |
| `USE_PROXY` | Optional. Set to `1`, `true`, or `yes` to enable proxy for pyRealtor (e.g. if REALTOR.CA rate-limits). |

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

The server exposes three tools: `ask_user`, `rental_search`, `simulate_viewing_request`. It uses stdio by default.

### Chat client (CLI)

Runs the agent loop with a CLI: you type your search, the LLM calls tools; when it calls `ask_user`, you are prompted for answers or multi-select in the terminal.

```bash
set OPENAI_API_KEY=your-key
rental-search-client
```

Or:

```bash
python -m rental_search_agent.client
```

You can set `OPENAI_MODEL` to use a different model (e.g. `gpt-4o`).

## Backend

- **In scope:** Single backend **pyRealtor** (REALTOR.CA, Canada/Vancouver). No other backends.
- **Out of scope:** Apify and the “Realtor.ca Property Search Scraper” actor are **not** implemented or used in this MVP.

## Docs

- [Technical spec](docs/rental-search-assistant-mvp-technical-spec.md)
- [MVP overview](docs/rental-search-assistant-mvp.md)

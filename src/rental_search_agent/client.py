"""Chat UI + agent runner. In-process tool execution; ask_user resolved via CLI."""

import json
import os
import sys
from pathlib import Path

from openai import OpenAI

from rental_search_agent.adapter import SearchBackendError, search
from rental_search_agent.agent import flow_instructions
from rental_search_agent.models import RentalSearchFilters
from rental_search_agent.server import do_simulate_viewing_request

# Tool definitions for the LLM (OpenAI function-calling format)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user for clarification or approval. Single answer (allow_multiple=False) or multi-select (allow_multiple=True). For approval use choices = listing labels that include id (e.g. '[1] 123 Main St — $2800 (id: xyz)').",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Question or instruction shown to the user."},
                    "choices": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Predefined options. Omit for free text.",
                    },
                    "allow_multiple": {
                        "type": "boolean",
                        "description": "If true, user may select zero or more choices; if false, single answer.",
                        "default": False,
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rental_search",
            "description": "Run a single rental search. Requires min_bedrooms and location in filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {
                        "type": "object",
                        "description": "Rental search filters: min_bedrooms (int), location (str) required; optional max_bedrooms, min/max_bathrooms, min/max_sqft, rent_min, rent_max, listing_type (for_rent, for_sale, for_sale_or_rent).",
                        "properties": {
                            "min_bedrooms": {"type": "integer", "minimum": 0},
                            "max_bedrooms": {"type": "integer", "minimum": 0},
                            "min_bathrooms": {"type": "integer", "minimum": 0},
                            "max_bathrooms": {"type": "integer", "minimum": 0},
                            "min_sqft": {"type": "integer", "minimum": 0},
                            "max_sqft": {"type": "integer", "minimum": 0},
                            "rent_min": {"type": "number", "minimum": 0},
                            "rent_max": {"type": "number", "minimum": 0},
                            "location": {"type": "string"},
                            "listing_type": {"type": "string", "enum": ["for_rent", "for_sale", "for_sale_or_rent"]},
                        },
                        "required": ["min_bedrooms", "location"],
                    },
                },
                "required": ["filters"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_viewing_request",
            "description": "Simulate a viewing request (no real form POST). Use listing url, a timeslot string from user's preference, and user_details (name, email required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "listing_url": {"type": "string", "description": "Canonical URL of the listing."},
                    "timeslot": {"type": "string", "description": "Human-readable timeslot (e.g. Tuesday 6–8pm)."},
                    "user_details": {
                        "type": "object",
                        "description": "User details: name and email required; phone and preferred_times optional.",
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                            "phone": {"type": "string"},
                            "preferred_times": {"type": "string"},
                        },
                        "required": ["name", "email"],
                    },
                },
                "required": ["listing_url", "timeslot", "user_details"],
            },
        },
    },
]


def run_tool(name: str, arguments: dict) -> str:
    """Execute tool in-process and return JSON string result. For ask_user, returns request_user_input payload; caller must resolve via UI and pass back answer/selected."""
    if name == "ask_user":
        # Return payload for client to show UI and supply real result
        return json.dumps({
            "request_user_input": True,
            "prompt": arguments.get("prompt", ""),
            "choices": arguments.get("choices") or [],
            "allow_multiple": arguments.get("allow_multiple", False),
        })
    if name == "rental_search":
        try:
            f = RentalSearchFilters.model_validate(arguments["filters"])
        except Exception as e:
            return json.dumps({"error": f"Invalid filters: {e}"})
        try:
            use_proxy = os.environ.get("USE_PROXY", "").strip().lower() in ("1", "true", "yes")
            resp = search(f, use_proxy=use_proxy)
        except SearchBackendError as e:
            return json.dumps({"error": str(e)})
        return resp.model_dump_json()
    if name == "simulate_viewing_request":
        try:
            resp = do_simulate_viewing_request(
                arguments["listing_url"],
                arguments["timeslot"],
                arguments["user_details"],
            )
            return resp.model_dump_json()
        except ValueError as e:
            return json.dumps({"error": str(e)})
    return json.dumps({"error": f"Unknown tool: {name}"})


def prompt_user_for_ask_user(payload: dict) -> str:
    """Show prompt and choices in CLI; return JSON string of { answer } or { selected }."""
    prompt = payload.get("prompt", "")
    choices = payload.get("choices") or []
    allow_multiple = payload.get("allow_multiple", False)
    print("\n--- " + prompt + " ---")
    if choices:
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        if allow_multiple:
            print("Enter numbers separated by commas (e.g. 1,3), or 0 for none:")
        else:
            print("Enter number or your answer:")
    else:
        print("Enter your answer:")
    line = (sys.stdin.readline() or "").strip()
    if allow_multiple:
        if not line or line == "0":
            return json.dumps({"selected": []})
        try:
            indices = [int(x.strip()) for x in line.split(",")]
            selected = [choices[i - 1] for i in indices if 1 <= i <= len(choices)]
            return json.dumps({"selected": selected})
        except (ValueError, IndexError):
            return json.dumps({"selected": []})
    if choices and line.isdigit():
        idx = int(line)
        if 1 <= idx <= len(choices):
            return json.dumps({"answer": choices[idx - 1]})
    return json.dumps({"answer": line})


# OpenRouter: unified API for 400+ models (https://openrouter.ai/docs)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from path into os.environ if not already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


def _make_llm_client() -> tuple[OpenAI, str]:
    """Build LLM client and model name. Prefer OpenRouter if OPENROUTER_API_KEY is set."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openrouter_key:
        model = os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
        client = OpenAI(
            api_key=openrouter_key,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://github.com/smakamali/rental_search_agent",
                "X-Title": "Rental Search Assistant",
            },
        )
        return client, model
    if openai_key:
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        client = OpenAI(api_key=openai_key)
        return client, model
    print(
        "Set OPENROUTER_API_KEY (recommended, see https://openrouter.ai) or OPENAI_API_KEY to run the client.",
        file=sys.stderr,
    )
    sys.exit(1)


def run_agent_loop() -> None:
    """Run the chat loop: user message -> LLM -> tool calls -> resolve ask_user in CLI -> loop until reply."""
    project_root = Path(__file__).resolve().parent.parent.parent
    _load_env_file(project_root / ".env")
    client, model = _make_llm_client()
    messages: list[dict] = [
        {"role": "system", "content": flow_instructions()},
    ]
    print("Rental Search Assistant (CLI). Type your search request (e.g. '2 bed in Vancouver under 3000'). Empty line to quit.\n")
    while True:
        user_line = (input("You: ").strip() if sys.stdin.isatty() else (sys.stdin.readline() or "").strip())
        if not user_line:
            break
        messages.append({"role": "user", "content": user_line})
        while True:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
            msg = resp.choices[0].message
            if not msg:
                break
            if msg.tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                        }
                        for tc in msg.tool_calls
                    ],
                })
                for tc in msg.tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = run_tool(name, args)
                    if name == "ask_user":
                        payload = json.loads(result)
                        if payload.get("request_user_input"):
                            result = prompt_user_for_ask_user(payload)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                continue
            # No tool calls: final assistant reply
            if msg.content:
                print("\nAssistant:", msg.content)
            messages.append({"role": "assistant", "content": msg.content or ""})
            break
    print("Goodbye.")


def main() -> None:
    run_agent_loop()


if __name__ == "__main__":
    main()

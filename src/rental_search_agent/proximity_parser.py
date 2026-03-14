"""Parse free-text proximity preferences into structured ProximityRule list using LLM extraction."""

import json
import logging
from typing import List

from rental_search_agent.api_config import get_llm_client_and_model
from rental_search_agent.models import ProximityRule

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Extract proximity rules from the user text. "
    "Return a JSON object with a single key 'rules' containing an array of objects. "
    "Each object must have exactly these fields: "
    "  'location' (string: the destination, e.g. 'Downtown Vancouver'; "
    "    for any transit stop, train station, subway, skytrain, or transit station "
    "    use exactly the string 'nearest transit station'), "
    "  'mode' (string: one of 'drive', 'walk', or 'transit'), "
    "  'max_minutes' (number: the maximum travel time in minutes). "
    "Return only the JSON object with no explanation."
)


def parse_proximity_preferences(proximity_text: str) -> List[ProximityRule]:
    """Parse free-text proximity preferences into a list of ProximityRule using LLM extraction.

    Handles any natural phrasing, for example:
      - "max 30 min drive to downtown Vancouver"
      - "5 min walk to skytrain"
      - "within 15 minutes driving to UBC; no more than 5 min walk to a train station"
      - "15 min by car to work and 3 min walk to transit"

    Raises ValueError if the LLM call fails or returns unparseable output.
    """
    if not proximity_text or not proximity_text.strip():
        return []

    client, model = get_llm_client_and_model()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": proximity_text.strip()},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
    except Exception as e:
        logger.error("parse_proximity_preferences: LLM call failed: %s", e)
        raise ValueError(f"Failed to parse proximity preferences: {e}") from e

    content = (response.choices[0].message.content or "{}").strip()
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        logger.error("parse_proximity_preferences: LLM returned invalid JSON: %r", content)
        raise ValueError(f"LLM returned invalid JSON: {e}") from e

    rules_raw = raw.get("rules") if isinstance(raw, dict) else raw
    if not isinstance(rules_raw, list):
        logger.warning("parse_proximity_preferences: unexpected shape %r", raw)
        return []

    rules: List[ProximityRule] = []
    seen: set[tuple[str, str, float]] = set()
    for item in rules_raw:
        try:
            rule = ProximityRule.model_validate(item)
            key = (rule.location, rule.mode, rule.max_minutes)
            if key not in seen:
                seen.add(key)
                rules.append(rule)
        except Exception as e:
            logger.warning("parse_proximity_preferences: skipping invalid rule %r: %s", item, e)

    logger.debug("parse_proximity_preferences: extracted %d rule(s) from %r", len(rules), proximity_text)
    return rules

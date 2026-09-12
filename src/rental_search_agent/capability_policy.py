"""Guest vs authenticated capability gates (search credits, multi-city, proximity)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from rental_search_agent.auth_principal import Principal


SIGN_IN_HINT = "Sign in with Google to unlock this feature."
MULTI_CITY_HINT = (
    "Multi-city metro search is available after you sign in with Google."
)
PROXIMITY_HINT = (
    "Guests can use at most {max_rules} proximity preference"
    "{plural}. Sign in with Google for more, or reduce your proximity preferences."
)
SEARCH_LIMIT_HINT = (
    "You've used all {max_searches} free searches this session. "
    "Sign in with Google for unlimited searches."
)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def anon_max_searches() -> int:
    return _env_int("ANON_MAX_SEARCHES", 3)


def anon_max_proximity_rules() -> int:
    return _env_int("ANON_MAX_PROXIMITY_RULES", 1)


@dataclass
class CapabilityPolicy:
    """Evaluate guest limits. Authenticated/dev principals are unrestricted."""

    principal: Principal
    searches_used: int = 0
    has_results: bool = False

    @property
    def max_searches(self) -> int:
        return anon_max_searches()

    @property
    def max_proximity_rules(self) -> int:
        return anon_max_proximity_rules()

    def remaining_searches(self) -> int | None:
        """None means unlimited."""
        if self.principal.has_full_access:
            return None
        return max(0, self.max_searches - max(0, self.searches_used))

    def can_scrape(self) -> bool:
        if self.principal.has_full_access:
            return True
        rem = self.remaining_searches()
        return rem is not None and rem > 0

    def record_scrape(self) -> int:
        """Increment guest scrape counter; return new searches_used."""
        if self.principal.has_full_access:
            return self.searches_used
        self.searches_used = max(0, self.searches_used) + 1
        return self.searches_used

    def can_multi_city(self) -> bool:
        return self.principal.has_full_access

    def can_proximity_rules(self, n_rules: int) -> bool:
        if self.principal.has_full_access:
            return True
        return n_rules <= self.max_proximity_rules

    def can_score(self) -> bool:
        """Scoring allowed for full users, or guests who have scraped / have results."""
        if self.principal.has_full_access:
            return True
        return self.searches_used > 0 or self.has_results

    def can_analyze(self) -> bool:
        return self.can_score()

    def scrape_denied_message(self) -> str:
        return SEARCH_LIMIT_HINT.format(max_searches=self.max_searches)

    def multi_city_denied_message(self) -> str:
        return MULTI_CITY_HINT

    def proximity_denied_message(self) -> str:
        max_rules = self.max_proximity_rules
        return PROXIMITY_HINT.format(
            max_rules=max_rules,
            plural="" if max_rules == 1 else "s",
        )

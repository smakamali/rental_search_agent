"""Optional Google-account allowlist for full (authenticated) access."""

from __future__ import annotations

import os


def allowlist_enabled() -> bool:
    raw = (os.environ.get("AUTH_ALLOWLIST_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def parse_allowlist(raw: str | None = None) -> tuple[set[str], set[str]]:
    """Return (emails, domains) from AUTH_ALLOWLIST.

    Entries may be full emails (user@example.com) or domains (@example.com / example.com).
    """
    text = raw if raw is not None else (os.environ.get("AUTH_ALLOWLIST") or "")
    emails: set[str] = set()
    domains: set[str] = set()
    for part in text.split(","):
        item = part.strip().lower()
        if not item:
            continue
        if item.startswith("@"):
            domains.add(item[1:])
            continue
        if "@" in item:
            emails.add(item)
            # Also allow treating bare domain-looking tokens without leading @
            continue
        # Bare domain without @
        domains.add(item)
    return emails, domains


def email_on_allowlist(email: str | None, *, raw_list: str | None = None) -> bool:
    """True if allowlist is disabled, or email matches an entry.

    When allowlist is enabled and the list is empty, nobody matches (fail closed).
    """
    if not allowlist_enabled():
        return True
    emails, domains = parse_allowlist(raw_list)
    if not emails and not domains:
        return False
    addr = (email or "").strip().lower()
    if not addr or "@" not in addr:
        return False
    if addr in emails:
        return True
    domain = addr.rsplit("@", 1)[-1]
    return domain in domains

"""Unit tests for allowlist, principal resolution, and CapabilityPolicy."""

from __future__ import annotations

import pytest

from rental_search_agent.auth_allowlist import email_on_allowlist, parse_allowlist
from rental_search_agent.auth_principal import Principal, current_principal
from rental_search_agent.capability_policy import CapabilityPolicy
from rental_search_agent.streamlit_app import _account_initials, _clear_analysis_selection
from rental_search_agent.session_runtime import (
    clear_runtime,
    merge_guest_prefs_on_login,
    save_active_preferences,
    set_runtime,
)
from rental_search_agent.preference_store import (
    SqlitePreferenceStore,
    empty_preferences,
    prefs_are_empty,
    reset_preference_store_cache,
)


@pytest.fixture(autouse=True)
def _clear_runtime():
    clear_runtime()
    reset_preference_store_cache()
    yield
    clear_runtime()
    reset_preference_store_cache()


class TestAllowlist:
    def test_disabled_allows_anyone(self, monkeypatch):
        monkeypatch.setenv("AUTH_ALLOWLIST_ENABLED", "false")
        assert email_on_allowlist("anyone@x.com")

    def test_enabled_empty_fails_closed(self, monkeypatch):
        monkeypatch.setenv("AUTH_ALLOWLIST_ENABLED", "true")
        monkeypatch.setenv("AUTH_ALLOWLIST", "")
        assert not email_on_allowlist("a@x.com")

    def test_email_and_domain(self, monkeypatch):
        monkeypatch.setenv("AUTH_ALLOWLIST_ENABLED", "true")
        monkeypatch.setenv("AUTH_ALLOWLIST", "Ada@X.com, @corp.com")
        emails, domains = parse_allowlist()
        assert "ada@x.com" in emails
        assert "corp.com" in domains
        assert email_on_allowlist("ada@x.com")
        assert email_on_allowlist("bob@corp.com")
        assert not email_on_allowlist("eve@other.com")


class TestPrincipal:
    def test_dev_when_auth_not_configured(self):
        p = current_principal(auth_configured=False)
        assert p.is_dev
        assert p.has_full_access

    def test_guest_when_dev_principal_disabled(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_PRINCIPAL", "false")
        p = current_principal(auth_configured=False)
        assert p.is_guest
        assert not p.has_full_access

    def test_guest_when_configured_not_logged_in(self, monkeypatch):
        class U:
            is_logged_in = False

        monkeypatch.setattr(
            "rental_search_agent.auth_principal._read_st_user",
            lambda: U(),
        )
        p = current_principal(auth_configured=True)
        assert p.is_guest

    def test_authenticated_when_allowlist_off(self, monkeypatch):
        monkeypatch.setenv("AUTH_ALLOWLIST_ENABLED", "false")

        class U:
            is_logged_in = True
            email = "a@x.com"
            name = "Ada"
            picture = ""
            sub = "sub-ada"

        monkeypatch.setattr(
            "rental_search_agent.auth_principal._read_st_user",
            lambda: U(),
        )
        p = current_principal(auth_configured=True)
        assert p.is_authenticated
        assert p.user_id == "sub-ada"

    def test_allowlist_denied_becomes_guest(self, monkeypatch):
        monkeypatch.setenv("AUTH_ALLOWLIST_ENABLED", "true")
        monkeypatch.setenv("AUTH_ALLOWLIST", "ok@x.com")

        class U:
            is_logged_in = True
            email = "nope@x.com"
            name = "Nope"
            picture = ""
            sub = "sub-nope"

        monkeypatch.setattr(
            "rental_search_agent.auth_principal._read_st_user",
            lambda: U(),
        )
        p = current_principal(auth_configured=True)
        assert p.is_guest
        assert p.allowlist_denied


class TestCapabilityPolicy:
    def test_guest_scrape_limit(self, monkeypatch):
        monkeypatch.setenv("ANON_MAX_SEARCHES", "2")
        guest = Principal(kind="guest")
        policy = CapabilityPolicy(principal=guest, searches_used=0)
        assert policy.can_scrape()
        assert policy.remaining_searches() == 2
        policy.record_scrape()
        policy.record_scrape()
        assert not policy.can_scrape()
        assert policy.remaining_searches() == 0

    def test_guest_multi_city_blocked(self):
        policy = CapabilityPolicy(principal=Principal(kind="guest"))
        assert not policy.can_multi_city()
        assert CapabilityPolicy(principal=Principal(kind="authenticated", user_id="x")).can_multi_city()

    def test_proximity_cap(self, monkeypatch):
        monkeypatch.setenv("ANON_MAX_PROXIMITY_RULES", "1")
        policy = CapabilityPolicy(principal=Principal(kind="guest"))
        assert policy.can_proximity_rules(1)
        assert not policy.can_proximity_rules(2)

    def test_score_after_scrape_or_results(self):
        guest = Principal(kind="guest")
        assert not CapabilityPolicy(principal=guest, searches_used=0).can_score()
        assert CapabilityPolicy(principal=guest, searches_used=1).can_score()
        assert CapabilityPolicy(principal=guest, searches_used=0, has_results=True).can_analyze()


class TestSessionRuntimePrefs:
    def test_guest_save_updates_holder_only(self, tmp_path, monkeypatch):
        reset_preference_store_cache()
        monkeypatch.setenv("PREFS_DB_PATH", str(tmp_path / "p.db"))
        holder = empty_preferences()
        set_runtime(Principal(kind="guest"), prefs_holder=holder)
        save_active_preferences({"location": "Vancouver"})
        assert holder["location"] == "Vancouver"
        # No durable sqlite write for guests: fresh store load for a user id is empty
        store = SqlitePreferenceStore(tmp_path / "p.db")
        assert prefs_are_empty(store.load("someone"))
        clear_runtime()

    def test_merge_guest_prefs_on_login(self, tmp_path, monkeypatch):
        reset_preference_store_cache()
        monkeypatch.setenv("PREFS_DB_PATH", str(tmp_path / "p.db"))
        principal = Principal(
            kind="authenticated",
            user_id="sub-1",
            email="a@x.com",
            name="Ada",
        )
        merged = merge_guest_prefs_on_login(principal, {"location": "Toronto"})
        assert merged["location"] == "Toronto"
        # Second login with empty guest should keep stored
        again = merge_guest_prefs_on_login(principal, empty_preferences())
        assert again["location"] == "Toronto"
        # Non-empty stored must not be clobbered by guest
        clobber = merge_guest_prefs_on_login(principal, {"location": "Montreal"})
        assert clobber["location"] == "Toronto"
        clear_runtime()
        reset_preference_store_cache()


class TestHeaderAccountHelpers:
    def test_account_initials(self):
        assert _account_initials(Principal(kind="authenticated", name="Amin K")) == "AK"
        assert _account_initials(Principal(kind="authenticated", email="ada@x.com")) == "AD"
        assert _account_initials(Principal(kind="guest")) == "?"

    def test_clear_analysis_selection(self, monkeypatch):
        ss: dict = {
            "analyze_listing_id": "R1",
            "analyze_listing": {"id": "R1"},
            "analysis_result": {"R1": {"match_score_pct": 80}},
        }

        class _FakeSt:
            session_state = ss

        monkeypatch.setattr("rental_search_agent.streamlit_app.st", _FakeSt)
        _clear_analysis_selection()
        assert ss["analyze_listing_id"] is None
        assert ss["analyze_listing"] is None
        assert "R1" in ss["analysis_result"]
        ss["analysis_result"] = {"R1": {"error": "x"}}
        ss["analyze_listing_id"] = "R1"
        _clear_analysis_selection(wipe_cache=True)
        assert ss["analysis_result"] == {}

"""Unit tests for PreferenceStore implementations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rental_search_agent.preference_resolution import PREF_KEYS
from rental_search_agent.preference_store import (
    LOCAL_USER_ID,
    FilePreferenceStore,
    SqlitePreferenceStore,
    empty_preferences,
    normalize_preferences,
    prefs_are_empty,
    reset_preference_store_cache,
)


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_preference_store_cache()
    yield
    reset_preference_store_cache()


class TestNormalizeAndEmpty:
    def test_normalize_fills_keys(self):
        out = normalize_preferences({"name": "Ada", "extra": "x"})
        assert out["name"] == "Ada"
        assert set(out) == set(PREF_KEYS)
        assert "extra" not in out

    def test_prefs_are_empty(self):
        assert prefs_are_empty(None)
        assert prefs_are_empty({})
        assert prefs_are_empty(empty_preferences())
        assert not prefs_are_empty({"location": "Vancouver"})


class TestFilePreferenceStore:
    def test_missing_file_returns_defaults(self, tmp_path: Path):
        store = FilePreferenceStore(tmp_path / "prefs.json")
        assert store.load(LOCAL_USER_ID) == empty_preferences()

    def test_roundtrip(self, tmp_path: Path):
        path = tmp_path / "prefs.json"
        store = FilePreferenceStore(path)
        store.save(LOCAL_USER_ID, {"location": "Toronto", "min_bedrooms": "2"})
        loaded = store.load("ignored")
        assert loaded["location"] == "Toronto"
        assert loaded["min_bedrooms"] == "2"
        assert path.exists()

    def test_malformed_json_returns_defaults(self, tmp_path: Path):
        path = tmp_path / "prefs.json"
        path.write_text("{not-json", encoding="utf-8")
        store = FilePreferenceStore(path)
        assert store.load(LOCAL_USER_ID) == empty_preferences()

    def test_save_failure_is_soft(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        path = tmp_path / "prefs.json"
        store = FilePreferenceStore(path)

        def boom(*_a, **_k):
            raise OSError("denied")

        monkeypatch.setattr(Path, "write_text", boom)
        store.save(LOCAL_USER_ID, {"name": "X"})
        assert not path.exists()


class TestSqlitePreferenceStore:
    def test_roundtrip_and_upsert(self, tmp_path: Path):
        db = tmp_path / "prefs.db"
        store = SqlitePreferenceStore(db)
        store.upsert_user("sub-1", email="a@x.com", name="Ada", picture_url="http://pic")
        store.save("sub-1", {"location": "Vancouver", "budget_max": "3000"})
        loaded = store.load("sub-1")
        assert loaded["location"] == "Vancouver"
        assert loaded["budget_max"] == "3000"
        assert store.load("missing") == empty_preferences()

        store.upsert_user("sub-1", email="a2@x.com", name="Ada L", picture_url="")
        store.save("sub-1", {"location": "Burnaby"})
        assert store.load("sub-1")["location"] == "Burnaby"

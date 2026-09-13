"""Tests for Analyze UI package assets and icon helpers."""

from __future__ import annotations

import base64

import pytest

from rental_search_agent import ui_assets
from rental_search_agent.ui_assets import (
    ANALYSIS_ICON_DIR,
    ANALYSIS_ICONS,
    analysis_icon_path,
    highlight_icon_img_html,
    icon_data_uri,
    normalize_highlight_icon_key,
    normalize_status_icon_key,
    status_icon_img_html,
)


class TestAnalysisIconAssets:
    def test_all_declared_icon_files_exist(self):
        assert ANALYSIS_ICON_DIR.is_dir()
        for key, filename in ANALYSIS_ICONS.items():
            path = ANALYSIS_ICON_DIR / filename
            assert path.is_file(), f"missing asset for {key}: {path}"
            assert path.suffix.lower() == ".png"
            # Optimized UI icons should be small.
            assert path.stat().st_size < 80_000

    def test_status_maps_to_correct_asset(self):
        assert analysis_icon_path("met").name == "status_met.png"
        assert analysis_icon_path("unmet").name == "status_unmet.png"
        assert analysis_icon_path("partial").name == "status_partial.png"
        assert analysis_icon_path("unknown").name == "status_unknown.png"

    def test_highlight_maps_to_correct_asset(self):
        assert analysis_icon_path("commute").name == "highlight_commute.png"
        assert analysis_icon_path("space").name == "highlight_space.png"
        assert analysis_icon_path("budget").name == "highlight_budget.png"
        assert analysis_icon_path("parking").name == "highlight_parking.png"
        assert analysis_icon_path("generic").name == "highlight_generic.png"

    def test_unknown_highlight_kind_maps_to_generic(self):
        assert normalize_highlight_icon_key("balcony") == "generic"
        assert normalize_highlight_icon_key(None) == "generic"
        assert normalize_highlight_icon_key("other") == "generic"
        assert analysis_icon_path("other").name == "highlight_generic.png"

    def test_arbitrary_filesystem_paths_rejected(self):
        assert analysis_icon_path("../secrets.toml") is None
        assert analysis_icon_path("/etc/passwd") is None
        assert analysis_icon_path("C:\\\\Windows\\\\system32") is None
        assert icon_data_uri("../secrets.toml") is None
        assert icon_data_uri("status_met.png") is None  # filename alone is not a key

    def test_icon_data_uri_returns_png_data_uri(self):
        uri = icon_data_uri("met")
        assert uri is not None
        assert uri.startswith("data:image/png;base64,")
        payload = uri.split(",", 1)[1]
        raw = base64.b64decode(payload)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    def test_icon_data_uri_is_cached(self):
        icon_data_uri.cache_clear()
        a = icon_data_uri("budget")
        b = icon_data_uri("budget")
        assert a == b
        assert icon_data_uri.cache_info().hits >= 1

    def test_missing_asset_graceful_fallback(self, monkeypatch, tmp_path):
        icon_data_uri.cache_clear()
        monkeypatch.setattr(ui_assets, "ANALYSIS_ICON_DIR", tmp_path)
        assert icon_data_uri("met") is None
        html = status_icon_img_html("met")
        assert "✓" in html
        assert "<img" not in html
        icon_data_uri.cache_clear()

    def test_status_and_highlight_html_use_img_when_present(self):
        status_html = status_icon_img_html("unmet", size_px=20)
        assert 'class="rsa-status-icon"' in status_html
        assert 'width="20"' in status_html
        assert 'alt=""' in status_html
        highlight_html = highlight_icon_img_html("commute", size_px=40)
        assert 'class="rsa-highlight-icon-img"' in highlight_html
        assert 'width="40"' in highlight_html

    def test_normalize_status(self):
        assert normalize_status_icon_key("met") == "met"
        assert normalize_status_icon_key("bogus") == "unknown"

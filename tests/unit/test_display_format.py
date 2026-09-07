"""Tests for display formatting helpers used by listing analysis."""

from rental_search_agent.display_format import (
    format_count,
    format_criterion_comparison,
    format_currency,
    format_duration,
    format_percentage,
    format_sqft,
    get_score_color,
    score_to_pct,
    split_listing_address,
)


class TestFormatCurrency:
    def test_million_not_scientific(self):
        assert format_currency(1_000_000) == "$1,000,000"
        assert format_currency(1_000_000.0) == "$1,000,000"
        assert "e+" not in format_currency(1_000_000).lower()
        assert "e+" not in format_currency(1000000.0).lower()

    def test_typical_price(self):
        assert format_currency(879_000) == "$879,000"

    def test_none_and_nan(self):
        assert format_currency(None) == "—"
        assert format_currency(float("nan")) == "—"
        assert format_currency("") == "—"


class TestFormatOthers:
    def test_duration(self):
        assert format_duration(2) == "2 min"
        assert format_duration(14.4) == "14 min"
        assert format_duration(None) == "—"

    def test_sqft(self):
        assert format_sqft(812) == "812 sq ft"
        assert format_sqft(750.0) == "750 sq ft"

    def test_percentage(self):
        assert format_percentage(90) == "90%"
        assert format_percentage(0.9, from_fraction=True) == "90%"

    def test_count(self):
        assert format_count(2.0) == "2"
        assert format_count(1.5) == "1.5"


class TestScoreColor:
    def test_pct_conversion(self):
        assert score_to_pct(0.9) == 90
        assert score_to_pct(90) == 90
        assert score_to_pct(None) is None

    def test_higher_scores_move_toward_green(self):
        def rgb(hex_color: str) -> tuple[int, int, int]:
            return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)

        r_low, g_low, _ = rgb(get_score_color(40))
        r_high, g_high, _ = rgb(get_score_color(95))
        assert g_high / max(r_high, 1) > g_low / max(r_low, 1)

    def test_no_red_hue_for_low_scores(self):
        # Low scores should be orange/amber, not failure-red.
        color = get_score_color(40)
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        assert r > 150 and g > 80
        assert r - b > 40


class TestComparisonAndAddress:
    def test_comparison(self):
        assert format_criterion_comparison("$879,000", "≤", "$1,000,000") == "$879,000 ≤ $1,000,000"
        assert format_criterion_comparison("2 min", "≤", "5 min") == "2 min ≤ 5 min"
        assert format_criterion_comparison("14 min", "≤", "30 min") == "14 min ≤ 30 min"
        assert format_criterion_comparison(None, None, None) == "Not mentioned"
        assert format_criterion_comparison("2", None, "2–3") == "2 (2–3)"

    def test_split_address(self):
        headline, locality = split_listing_address(
            "3008 939 EXPO BOULEVARD, Vancouver, British Columbia V6Z3G7",
            "V6Z3G7",
        )
        assert headline == "3008 939 EXPO BOULEVARD"
        assert "Vancouver" in locality

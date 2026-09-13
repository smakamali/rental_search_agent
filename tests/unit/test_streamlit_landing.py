"""Unit tests for landing-page helpers, example prompts, and empty-state detection."""

from rental_search_agent.streamlit_landing import (
    CHAT_STARTER_PROMPTS,
    LANDING_EXAMPLES,
    _SVG_ICONS,
    _preference_chip_html,
    center_panel_kind,
    chat_starter_has_required_criteria,
    format_landing_baths_chip,
    format_landing_beds_chip,
    format_landing_budget_chip,
    format_landing_sqft_chip,
    has_visible_chat_history,
    landing_preference_chips,
    saved_preferences_ready_for_search,
    search_has_run,
    should_render_chat_empty_state,
)


class TestLandingPreferenceSummary:
    def _example_prefs(self, **overrides):
        prefs = {
            "location": "Vancouver",
            "listing_type": "for_sale",
            "budget_max": 1000000,
            "min_bedrooms": 2,
            "min_bathrooms": 2,
            "min_sqft": 850,
        }
        prefs.update(overrides)
        return prefs

    def test_example_prefs_produce_friendly_chips(self):
        chips = landing_preference_chips(self._example_prefs())
        labels = [label for _kind, label in chips]
        assert "Vancouver" in labels
        assert "Buy" in labels
        assert "≤ $1M" in labels
        assert "2 beds" in labels
        assert "2 baths" in labels
        assert "≥ 850 sq ft" in labels

    def test_empty_values_omitted(self):
        chips = landing_preference_chips(
            {
                "location": "",
                "listing_type": "",
                "budget_max": None,
                "min_bedrooms": "",
                "max_bedrooms": None,
                "min_bathrooms": "",
                "min_sqft": "",
            }
        )
        assert chips == []

    def test_listing_type_buy_and_rent(self):
        buy = landing_preference_chips({"listing_type": "for_sale"})
        rent = landing_preference_chips({"listing_type": "for_rent"})
        assert ("listing_type", "Buy") in buy
        assert ("listing_type", "Rent") in rent

    def test_raw_budget_float_never_appears(self):
        labels = [label for _k, label in landing_preference_chips(self._example_prefs())]
        joined = " ".join(labels)
        assert "1000000.0" not in joined
        assert "1000000" not in joined
        chip = format_landing_budget_chip(1000000.0)
        assert chip == "≤ $1M"
        assert chip is not None
        assert "1000000" not in chip

    def test_bedroom_range_when_present(self):
        chips = landing_preference_chips(
            {"min_bedrooms": 2, "max_bedrooms": 3}
        )
        assert ("beds", "2–3 beds") in chips

    def test_same_min_max_beds_is_not_a_range(self):
        chips = landing_preference_chips(
            {"min_bedrooms": 2, "max_bedrooms": 2}
        )
        assert ("beds", "2 beds") in chips
        assert not any("–" in label for _k, label in chips)

    def test_compact_non_million_budget(self):
        assert format_landing_budget_chip("2800") == "≤ $2,800"
        assert format_landing_budget_chip("") is None
        assert format_landing_budget_chip(None) is None

    def test_chip_html_escapes_user_text(self):
        out = _preference_chip_html("location", '<script>alert(1)</script>')
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_listing_type_chip_uses_home_icon(self):
        out = _preference_chip_html("listing_type", "Buy")
        assert _SVG_ICONS["home"] in out
        assert "Buy" in out

    def test_none_prefs_yield_no_chips(self):
        assert landing_preference_chips(None) == []

    def test_beds_max_only(self):
        assert format_landing_beds_chip(None, 3) == "3 beds"
        assert format_landing_beds_chip("", 4) == "4 beds"

    def test_baths_and_sqft_helpers(self):
        assert format_landing_baths_chip(2) == "2 baths"
        assert format_landing_baths_chip(None) is None
        assert format_landing_baths_chip("") is None
        assert format_landing_sqft_chip(850) == "≥ 850 sq ft"
        assert format_landing_sqft_chip(None) is None
        assert format_landing_sqft_chip("") is None

    def test_format_helpers_none_inputs(self):
        assert format_landing_beds_chip(None, None) is None
        assert format_landing_budget_chip(None) is None


class TestChatStarterPrompts:
    def test_exact_three_prompts(self):
        assert len(CHAT_STARTER_PROMPTS) == 3
        assert CHAT_STARTER_PROMPTS[0] == "2 bed condo in Vancouver under $3,000"
        assert CHAT_STARTER_PROMPTS[1] == (
            "2 bed apartment in Burnaby with parking and balcony"
        )
        assert CHAT_STARTER_PROMPTS[2] == (
            "2 bed condo in Vancouver within 30 minutes of downtown, "
            "5 minutes walk from a public transit station"
        )

    def test_every_starter_has_beds_and_city(self):
        for prompt in CHAT_STARTER_PROMPTS:
            assert chat_starter_has_required_criteria(prompt), prompt

    def test_required_criteria_negatives(self):
        assert chat_starter_has_required_criteria("condo in Vancouver") is False
        assert chat_starter_has_required_criteria("2 bed condo") is False
        assert chat_starter_has_required_criteria("parking and balcony") is False
        assert chat_starter_has_required_criteria("") is False
        assert chat_starter_has_required_criteria(None) is False

    def test_no_skytrain(self):
        for prompt in CHAT_STARTER_PROMPTS:
            assert "skytrain" not in prompt.lower()


class TestLandingExamples:
    def test_first_prompt_is_exact_structural_text(self):
        assert LANDING_EXAMPLES[0].kind == "structural"
        assert LANDING_EXAMPLES[0].prompt == "2-bedroom condo in Vancouver under $1M"

    def test_distinct_from_chat_starters(self):
        landing_prompts = {ex.prompt for ex in LANDING_EXAMPLES}
        assert landing_prompts.isdisjoint(set(CHAT_STARTER_PROMPTS))

    def test_second_is_amenities(self):
        assert LANDING_EXAMPLES[1].kind == "amenities"
        assert "parking" in LANDING_EXAMPLES[1].prompt.lower()
        assert "balcony" in LANDING_EXAMPLES[1].prompt.lower()
        assert "auto_awesome" in LANDING_EXAMPLES[1].icon

    def test_third_is_proximity(self):
        assert LANDING_EXAMPLES[2].kind == "proximity"
        assert "30 minutes" in LANDING_EXAMPLES[2].prompt
        assert "transit" in LANDING_EXAMPLES[2].prompt.lower()
        assert "location_on" in LANDING_EXAMPLES[2].icon

    def test_no_skytrain(self):
        for example in LANDING_EXAMPLES:
            blob = f"{example.label} {example.prompt} {example.kind}"
            assert "skytrain" not in blob.lower()

    def test_amenities_icon_is_not_transit(self):
        icon = LANDING_EXAMPLES[1].icon.lower()
        for banned in ("train", "transit", "route", "location", "directions"):
            assert banned not in icon


class TestPreSearchDetection:
    def test_pristine_is_landing(self):
        assert search_has_run(display_source=None, search_master=[], last_filters=None) is False
        assert (
            center_panel_kind(
                listings=[],
                display_source=None,
                search_master=[],
                last_filters=None,
            )
            == "landing"
        )

    def test_search_with_results_is_results(self):
        assert (
            center_panel_kind(
                listings=[{"id": "a"}],
                display_source="score",
                search_master=[{"id": "a"}],
                last_filters={"location": "Vancouver"},
            )
            == "results"
        )

    def test_zero_results_is_not_welcome(self):
        assert search_has_run(display_source="filter", search_master=[], last_filters=None) is True
        assert (
            center_panel_kind(
                listings=[],
                display_source="filter",
                search_master=[],
                last_filters=None,
            )
            == "zero_results"
        )

    def test_zero_results_via_last_filters_only(self):
        assert (
            center_panel_kind(
                listings=[],
                display_source=None,
                search_master=[],
                last_filters={"location": "Vancouver", "min_bedrooms": 2},
            )
            == "zero_results"
        )

    def test_zero_results_via_search_master_only(self):
        assert (
            center_panel_kind(
                listings=[],
                display_source=None,
                search_master=[{"id": "filtered-out"}],
                last_filters=None,
            )
            == "zero_results"
        )


class TestVisibleChatHistory:
    def test_system_only_is_empty(self):
        assert has_visible_chat_history([{"role": "system", "content": "sys"}]) is False

    def test_system_and_tool_is_empty(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "tool", "tool_call_id": "t1", "content": "{}"},
        ]
        assert has_visible_chat_history(messages) is False

    def test_user_message_is_visible(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "2 bed condo in Vancouver"},
        ]
        assert has_visible_chat_history(messages) is True

    def test_assistant_text_is_visible(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "Here are some matches."},
        ]
        assert has_visible_chat_history(messages) is True

    def test_empty_assistant_tool_call_is_not_visible(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]},
            {"role": "tool", "content": "{}"},
        ]
        assert has_visible_chat_history(messages) is False

    def test_empty_state_hidden_when_prompt_queued(self):
        messages = [{"role": "system", "content": "sys"}]
        assert should_render_chat_empty_state(messages) is True
        assert (
            should_render_chat_empty_state(
                messages, pending_chat_prompt="2 bed condo in Vancouver"
            )
            is False
        )
        assert (
            should_render_chat_empty_state(
                messages, pending_ask={"question": "Which city?"}
            )
            is False
        )
        assert should_render_chat_empty_state(messages, pending_chat_prompt="") is True
        assert should_render_chat_empty_state(None) is True


class TestSavedPrefsReady:
    def test_requires_location_and_beds(self):
        assert saved_preferences_ready_for_search({}) is False
        assert saved_preferences_ready_for_search({"location": "Vancouver"}) is False
        assert saved_preferences_ready_for_search({"min_bedrooms": "2"}) is False
        assert (
            saved_preferences_ready_for_search(
                {"location": "Vancouver", "min_bedrooms": "2"}
            )
            is True
        )

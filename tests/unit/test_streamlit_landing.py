"""Unit tests for landing-page helpers, example prompts, and empty-state detection."""

import inspect

from rental_search_agent.streamlit_landing import (
    CHAT_STARTER_PROMPTS,
    HOW_IT_WORKS_HEADING,
    HOW_IT_WORKS_STEPS,
    HOW_IT_WORKS_SUBTITLE,
    LANDING_HERO_LEAD,
    _SVG_ICONS,
    _feature_chip_html,
    _how_it_works_html,
    _preference_chip_html,
    inject_landing_css,
    center_panel_kind,
    chat_starter_has_required_criteria,
    format_landing_baths_chip,
    format_landing_beds_chip,
    format_landing_budget_chip,
    format_landing_sqft_chip,
    has_visible_chat_history,
    landing_preference_chips,
    landing_saved_prefs_heading,
    missing_required_search_fields,
    render_landing_page,
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


class TestLandingHeroCopy:
    def test_hero_lead_is_the_approved_sentence(self):
        assert LANDING_HERO_LEAD == (
            "Set your preferences or search naturally in chat, then compare "
            "and analyze your best matches."
        )
        assert "example" not in LANDING_HERO_LEAD.lower()


class TestHowItWorks:
    def test_exactly_three_steps(self):
        assert len(HOW_IT_WORKS_STEPS) == 3

    def test_titles(self):
        assert [step.title for step in HOW_IT_WORKS_STEPS] == [
            "Set preferences",
            "Compare matches",
            "Analyze a property",
        ]

    def test_heading_and_subtitle(self):
        assert HOW_IT_WORKS_HEADING == "How it works"
        assert HOW_IT_WORKS_SUBTITLE == (
            "A simpler way to find, compare, and understand properties."
        )

    def test_step1_chips(self):
        assert HOW_IT_WORKS_STEPS[0].chips == (
            "Location",
            "Budget",
            "Bedrooms",
            "Amenities",
            "Proximity",
        )

    def test_step2_chips(self):
        assert HOW_IT_WORKS_STEPS[1].chips == (
            "Grid",
            "Table",
            "Map",
            "Match score",
        )

    def test_step3_chips(self):
        assert HOW_IT_WORKS_STEPS[2].chips == (
            "Checklist",
            "Score breakdown",
            "Highlights",
            "Open questions",
        )

    def test_step_copy_matches_approved_text(self):
        assert HOW_IT_WORKS_STEPS[0].copy == (
            "Define the requirements that matter to you or use your saved "
            "preferences."
        )
        assert HOW_IT_WORKS_STEPS[1].copy == (
            "See matched properties in Grid, Table, or Map view and compare "
            "key details at a glance."
        )
        assert HOW_IT_WORKS_STEPS[2].copy == (
            "Open Analyze to understand why a property matches, where the "
            "evidence comes from, and what may still be missing."
        )

    def test_html_includes_cards_icons_and_chips(self):
        out = _how_it_works_html()
        assert 'class="rsa-landing-how"' in out
        assert out.count("rsa-landing-how-card") == 3
        assert _SVG_ICONS["prefs"] in out
        assert _SVG_ICONS["compare"] in out
        assert _SVG_ICONS["analyze"] in out
        for step in HOW_IT_WORKS_STEPS:
            assert step.title in out
            assert step.copy in out
            for chip in step.chips:
                assert chip in out
        assert "rsa-landing-feature-chip" in out
        assert "Try an example" not in out

    def test_feature_chip_html_escapes_text(self):
        out = _feature_chip_html('<script>alert(1)</script>')
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_render_landing_page_no_longer_takes_on_example(self):
        params = inspect.signature(render_landing_page).parameters
        assert "on_example" not in params
        assert "on_search" in params
        assert "on_ask_in_chat" in params

    def test_landing_css_drops_central_examples_and_keeps_chat_chevrons(self):
        source = inspect.getsource(inject_landing_css)
        assert "rsa_landing_examples" not in source
        assert "rsa_landing_ex_" not in source
        assert "rsa_chat_starter_" in source
        assert "rsa-landing-feature-chip" in source
        assert "rsa-landing-how-card" in source


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

    def test_missing_fields_match_readiness_rules(self):
        assert missing_required_search_fields(None) == ("location", "min_bedrooms")
        assert missing_required_search_fields({}) == ("location", "min_bedrooms")
        assert missing_required_search_fields({"location": "  "}) == (
            "location",
            "min_bedrooms",
        )
        assert missing_required_search_fields({"location": "Vancouver"}) == (
            "min_bedrooms",
        )
        assert missing_required_search_fields({"min_bedrooms": "2"}) == ("location",)
        assert (
            missing_required_search_fields(
                {"location": "Vancouver", "min_bedrooms": "2"}
            )
            == ()
        )

    def test_heading_when_ready(self):
        assert landing_saved_prefs_heading(
            {"location": "Yaletown", "min_bedrooms": 2}
        ) == "Ready to search with your saved preferences"

    def test_heading_when_location_missing(self):
        assert landing_saved_prefs_heading({"min_bedrooms": 2}) == (
            "Add a location to search with your saved preferences"
        )

    def test_heading_when_beds_missing(self):
        assert landing_saved_prefs_heading({"location": "Yaletown"}) == (
            "Add a minimum bedroom count to search with your saved preferences"
        )

    def test_heading_when_both_required_fields_missing(self):
        assert landing_saved_prefs_heading({}) == (
            "Add a location and minimum bedrooms to get started"
        )
        assert landing_saved_prefs_heading(None) == (
            "Add a location and minimum bedrooms to get started"
        )

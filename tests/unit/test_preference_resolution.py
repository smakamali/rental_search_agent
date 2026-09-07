"""Unit tests for preference merge, fill-in, and preferences_block."""

from rental_search_agent.preference_resolution import (
    PREF_KEYS,
    EffectiveSearchPreferences,
    fill_empty_stored_from_chat,
    merge_chat_over_stored,
    preferences_block,
    resolve_active_requirement,
    stored_prefs_to_effective,
)


class TestMergeChatOverStored:
    def test_chat_overrides_stored_budget(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["budget_max"] = "2500"
        stored["min_bedrooms"] = "2"
        effective = merge_chat_over_stored(stored, {"price_max": 3000, "min_bedrooms": 3})
        assert effective.budget_max == 3000
        assert effective.min_bedrooms == 3

    def test_stored_used_when_chat_omits(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["budget_max"] = "2500"
        stored["qualitative_preferences"] = "balcony"
        effective = merge_chat_over_stored(stored, {"min_bedrooms": 2})
        assert effective.budget_max == 2500
        assert effective.min_bedrooms == 2
        assert effective.qualitative_preferences == "balcony"

    def test_require_den_from_stored(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["require_den"] = "true"
        effective = stored_prefs_to_effective(stored)
        assert effective.require_den is True


class TestFillEmptyStoredFromChat:
    def test_fills_empty_only(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["budget_max"] = "2000"
        filled = fill_empty_stored_from_chat(
            stored,
            {"price_max": 3000, "min_bedrooms": 2, "qualitative_preferences": "parking"},
        )
        assert filled["budget_max"] == "2000"  # not overwritten
        assert filled["min_bedrooms"] == "2"
        assert filled["qualitative_preferences"] == "parking"

    def test_does_not_overwrite_nonempty_qualitative(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["qualitative_preferences"] = "balcony"
        filled = fill_empty_stored_from_chat(
            stored, {"qualitative_preferences": "parking"}
        )
        assert filled["qualitative_preferences"] == "balcony"


class TestPlaceholderQualitative:
    def test_placeholder_does_not_override_stored(self):
        from rental_search_agent.preference_resolution import is_placeholder_qualitative

        assert is_placeholder_qualitative("match my search preferences")
        stored = {k: "" for k in PREF_KEYS}
        stored["qualitative_preferences"] = "balcony, parking"
        effective = merge_chat_over_stored(
            stored, {"qualitative_preferences": "match my search preferences"}
        )
        assert effective.qualitative_preferences == "balcony, parking"


class TestResolveActiveRequirement:
    def test_chat_wins_over_stored_budget(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["budget_max"] = "900000"
        assert (
            resolve_active_requirement(
                "budget_max",
                active_search={"price_max": 1_000_000},
                stored_preferences=stored,
            )
            == 1_000_000
        )

    def test_stored_used_when_chat_omits(self):
        stored = {k: "" for k in PREF_KEYS}
        stored["min_bedrooms"] = "2"
        assert (
            resolve_active_requirement(
                "min_bedrooms",
                active_search={},
                stored_preferences=stored,
            )
            == 2
        )



class TestPreferencesBlock:
    def test_empty(self):
        prefs = {k: "" for k in PREF_KEYS}
        assert "No stored search preferences" in preferences_block(prefs)

    def test_includes_search_prefs_not_contact(self):
        prefs = {k: "" for k in PREF_KEYS}
        prefs["name"] = "Jane"
        prefs["budget_max"] = "2800"
        prefs["min_bedrooms"] = "2"
        prefs["qualitative_preferences"] = "balcony"
        block = preferences_block(prefs)
        assert "budget_max" in block
        assert "min_bedrooms" in block
        assert "qualitative_preferences" in block
        assert "name =" not in block
        assert "Jane" not in block

    def test_has_score_relevant(self):
        assert EffectiveSearchPreferences(budget_max=2000).has_score_relevant_prefs()
        assert not EffectiveSearchPreferences().has_score_relevant_prefs()


class TestQualitativeFromPreferencesText:
    def test_strips_proximity_block(self):
        from rental_search_agent.preference_resolution import qualitative_from_preferences_text

        text = "must have balcony, parking\n\nProximity: 5 min walk to a transit station"
        assert qualitative_from_preferences_text(text) == "must have balcony, parking"

    def test_placeholder_becomes_empty(self):
        from rental_search_agent.preference_resolution import qualitative_from_preferences_text

        assert qualitative_from_preferences_text("Match my search preferences") == ""

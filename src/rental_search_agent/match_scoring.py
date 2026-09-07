"""Multi-metric listing match scoring: coverage, structural, proximity, amenity, semantic."""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Any, Dict, List, Optional, Sequence, Union

from rental_search_agent.preference_criteria import (
    CriterionResult,
    evaluate_coverage,
    extract_amenity_features,
    listing_semantic_blob,
    qualitative_for_semantic,
    score_amenity,
    score_proximity,
    score_structural,
)
from rental_search_agent.preference_resolution import (
    EffectiveSearchPreferences,
    is_placeholder_qualitative,
    merge_chat_over_stored,
)
from rental_search_agent.scoring_config import (
    get_score_parallel_min_listings,
    get_score_parallel_workers,
    get_score_weights,
)
from rental_search_agent.semantic_scoring import _cosine_similarity, embed_texts

logger = logging.getLogger(__name__)

COMPONENT_KEYS = ("structural", "proximity", "amenity", "semantic")
# Coverage is still computed for checklist / Analyze UI but not folded into match_score.
BREAKDOWN_EXTRA_KEYS = ("coverage",)


def combine_component_scores(
    components: Dict[str, Optional[float]],
    weights: Optional[Dict[str, float]] = None,
) -> Optional[float]:
    """Weighted average over components that are not None; renormalize. None if none present."""
    weights = weights or get_score_weights()
    total_w = 0.0
    total = 0.0
    for key in COMPONENT_KEYS:
        score = components.get(key)
        if score is None:
            continue
        w = float(weights.get(key, 0.0))
        if w <= 0:
            continue
        total_w += w
        total += w * max(0.0, min(1.0, float(score)))
    if total_w <= 0:
        return None
    return round(total / total_w, 4)


def _to_dict(item: Any) -> dict:
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return dict(item)


def _criterion_to_dict(c: CriterionResult) -> dict:
    return {
        "id": c.id,
        "label": c.label,
        "status": c.status,
        "score": c.score,
        "weight": c.weight,
        "group": c.group,
        "name": c.name or c.label,
        "observed": c.observed,
        "required": c.required,
        "comparator": c.comparator,
        "source": c.source,
        "detail": c.detail,
    }


def _score_one_listing(
    listing: Any,
    prefs: EffectiveSearchPreferences,
    proximity_rules: Sequence[dict],
    amenity_features: list,
    semantic_score: Optional[float],
    weights: Dict[str, float],
) -> dict:
    d = _to_dict(listing)
    coverage, checklist = evaluate_coverage(prefs, d, proximity_rules, amenity_features)
    structural = score_structural(prefs, d)
    proximity = score_proximity(d, proximity_rules)
    amenity = score_amenity(d, amenity_features)
    components: Dict[str, Optional[float]] = {
        "coverage": round(coverage, 4) if coverage is not None else None,
        "structural": round(structural, 4) if structural is not None else None,
        "proximity": round(proximity, 4) if proximity is not None else None,
        "amenity": round(amenity, 4) if amenity is not None else None,
        "semantic": round(semantic_score, 4) if semantic_score is not None else None,
    }
    overall = combine_component_scores(components, weights)
    included = [k for k in COMPONENT_KEYS if components.get(k) is not None]
    # Renormalized shares so breakdown matches the actual overall formula.
    total_w = sum(float(weights.get(k, 0.0)) for k in included if float(weights.get(k, 0.0)) > 0)
    used_weights = {
        k: round(float(weights.get(k, 0.0)) / total_w, 4) if total_w > 0 else 0.0
        for k in included
    }
    d["semantic_score"] = components["semantic"]
    d["match_score"] = overall
    d["score_breakdown"] = {
        "components": components,
        "included": included,
        "weights_used": used_weights,
        "coverage": components.get("coverage"),
        "checklist": [_criterion_to_dict(c) for c in checklist],
    }
    return d


def _compute_semantic_scores(
    listings: List[Any],
    prefs: EffectiveSearchPreferences,
    embedding_model: Optional[str] = None,
) -> List[Optional[float]]:
    """Batch-embed qualitative prefs vs narrowed listing blobs. Omit (None) on failure or empty prefs."""
    query = qualitative_for_semantic(prefs)
    if not query:
        return [None] * len(listings)
    blobs = [listing_semantic_blob(item) for item in listings]
    blobs = [b if (b or "").strip() else " " for b in blobs]
    try:
        query_emb = embed_texts([query], model=embedding_model)[0]
        listing_embs = embed_texts(blobs, model=embedding_model)
    except Exception as e:
        logger.warning("Semantic component embedding failed (omitting semantic): %s", e)
        return [None] * len(listings)
    out: List[Optional[float]] = []
    for i in range(len(listings)):
        if i >= len(listing_embs):
            out.append(None)
            continue
        sim = _cosine_similarity(query_emb, listing_embs[i])
        out.append(max(0.0, min(1.0, sim)))
    return out


def score_listings_by_preferences(
    listings: List[Any],
    preferences_text: str = "",
    query_text: Optional[str] = None,
    embedding_model: Optional[str] = None,
    *,
    effective_prefs: Optional[EffectiveSearchPreferences] = None,
    stored_prefs: Optional[dict] = None,
    chat_criteria: Optional[dict] = None,
    proximity_rules: Optional[Sequence[dict]] = None,
) -> List[dict]:
    """Score listings with multi-metric match_score; sort by match_score desc (then semantic).

    Backward compatible: preferences_text is treated as qualitative when effective_prefs
    is not provided. Missing components are excluded from the weighted average.
    """
    if effective_prefs is None:
        stored = dict(stored_prefs or {})
        chat = dict(chat_criteria or {})
        qual = (preferences_text or "").strip()
        if (
            qual
            and not is_placeholder_qualitative(qual)
            and not (stored.get("qualitative_preferences") or chat.get("qualitative_preferences"))
        ):
            chat.setdefault("qualitative_preferences", qual)
        if query_text and not chat.get("qualitative_preferences"):
            extra = (query_text or "").strip()
            if extra and not is_placeholder_qualitative(extra):
                chat["qualitative_preferences"] = (
                    (chat.get("qualitative_preferences") or "") + " " + extra
                ).strip()
        effective_prefs = merge_chat_over_stored(stored, chat)
        if (
            not effective_prefs.qualitative_preferences
            and qual
            and not is_placeholder_qualitative(qual)
        ):
            effective_prefs = effective_prefs.model_copy(update={"qualitative_preferences": qual})
    elif is_placeholder_qualitative(effective_prefs.qualitative_preferences):
        # Never embed / amenity-parse UI/agent placeholders.
        effective_prefs = effective_prefs.model_copy(update={"qualitative_preferences": ""})

    rules = [r for r in (proximity_rules or []) if isinstance(r, dict)]
    amenity_features = extract_amenity_features(effective_prefs.qualitative_preferences or "")
    weights = get_score_weights()

    if not listings:
        return []

    # Nothing to score?
    if not effective_prefs.has_score_relevant_prefs() and not rules:
        return [_to_dict(item) for item in listings]

    semantic_scores = _compute_semantic_scores(listings, effective_prefs, embedding_model=embedding_model)

    workers = get_score_parallel_workers()
    min_n = get_score_parallel_min_listings()
    use_pool = workers > 1 and len(listings) >= min_n

    def _job(i: int) -> tuple[int, dict]:
        scored = _score_one_listing(
            listings[i],
            effective_prefs,
            rules,
            amenity_features,
            semantic_scores[i] if i < len(semantic_scores) else None,
            weights,
        )
        return i, scored

    if use_pool:
        results: List[Optional[dict]] = [None] * len(listings)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_job, i) for i in range(len(listings))]
            for fut in concurrent.futures.as_completed(futures):
                i, scored = fut.result()
                results[i] = scored
        scored_list = [r for r in results if r is not None]
    else:
        scored_list = [_job(i)[1] for i in range(len(listings))]

    def _sort_key(d: dict) -> tuple:
        ms = d.get("match_score")
        ss = d.get("semantic_score")
        # Higher is better; missing sorts last
        return (
            0 if ms is not None else 1,
            -(float(ms) if ms is not None else 0.0),
            0 if ss is not None else 1,
            -(float(ss) if ss is not None else 0.0),
        )

    scored_list.sort(key=_sort_key)
    return scored_list

"""Match-score component weights and parallelization knobs from environment.

Override via .env (non-negative floats). If all weights are zero or unreadable,
built-in defaults are used. Missing components are excluded from the weighted
average at score time (weights are renormalized over present components only).

Coverage is computed for the Analyze checklist / breakdown but is NOT part of
the overall weighted match_score (avoids double-counting with structural /
proximity / amenity).

  SCORE_WEIGHT_STRUCTURAL=0.35
  SCORE_WEIGHT_PROXIMITY=0.25
  SCORE_WEIGHT_AMENITY=0.20
  SCORE_WEIGHT_SEMANTIC=0.20
  SCORE_PARALLEL_WORKERS=4
  SCORE_PARALLEL_MIN_LISTINGS=8
"""

from __future__ import annotations

import os
from typing import Dict

# Coverage is intentionally omitted from overall weights (checklist-only metric).
DEFAULT_WEIGHTS: Dict[str, float] = {
    "structural": 0.35,
    "proximity": 0.25,
    "amenity": 0.20,
    "semantic": 0.20,
}

_ENV_KEYS = {
    "structural": "SCORE_WEIGHT_STRUCTURAL",
    "proximity": "SCORE_WEIGHT_PROXIMITY",
    "amenity": "SCORE_WEIGHT_AMENITY",
    "semantic": "SCORE_WEIGHT_SEMANTIC",
}


def _parse_nonneg_float(raw: str | None, default: float) -> float:
    if raw is None or not str(raw).strip():
        return default
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if val < 0:
        return default
    return val


def get_score_weights() -> Dict[str, float]:
    """Return component weight map for overall match_score. Falls back to defaults if all zero."""
    weights: Dict[str, float] = {}
    for key, env_name in _ENV_KEYS.items():
        weights[key] = _parse_nonneg_float(os.environ.get(env_name), DEFAULT_WEIGHTS[key])
    if sum(weights.values()) <= 0:
        return dict(DEFAULT_WEIGHTS)
    return weights


def get_score_parallel_workers() -> int:
    """Thread pool size for per-listing scoring. 1 disables parallelism."""
    raw = (os.environ.get("SCORE_PARALLEL_WORKERS") or "").strip()
    if not raw:
        return 4
    try:
        n = int(raw)
    except ValueError:
        return 4
    return max(1, n)


def get_score_parallel_min_listings() -> int:
    """Minimum listing count before enabling the thread pool."""
    raw = (os.environ.get("SCORE_PARALLEL_MIN_LISTINGS") or "").strip()
    if not raw:
        return 8
    try:
        n = int(raw)
    except ValueError:
        return 8
    return max(1, n)

"""Normalization utilities for factor scoring."""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def valid_numbers(values: Iterable[object]) -> np.ndarray:
    out = []
    for value in values:
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            out.append(float(value))
    return np.asarray(out, dtype=float)


def percentile_rank_normalize(
    value: Optional[float],
    universe_values: Iterable[object],
    winsorize_pct: float = 0.02,
) -> Optional[float]:
    if value is None or not math.isfinite(float(value)):
        return None
    universe = valid_numbers(universe_values)
    if len(universe) == 0:
        return None
    if len(universe) == 1 or float(np.nanmax(universe)) == float(np.nanmin(universe)):
        return 50.0

    lower = float(np.percentile(universe, winsorize_pct * 100))
    upper = float(np.percentile(universe, (1 - winsorize_pct) * 100))
    clipped_universe = np.clip(universe, lower, upper)
    clipped_value = float(np.clip(float(value), lower, upper))
    less = float(np.sum(clipped_universe < clipped_value))
    equal = float(np.sum(clipped_universe == clipped_value))
    rank = (less + 0.5 * equal) / len(clipped_universe) * 100
    return clamp(rank)


def normalize_inverted(
    value: Optional[float],
    universe_values: Iterable[object],
    winsorize_pct: float = 0.02,
) -> Optional[float]:
    score = percentile_rank_normalize(value, universe_values, winsorize_pct)
    return None if score is None else 100.0 - score


def min_max_normalize(value: Optional[float], min_val: float, max_val: float) -> Optional[float]:
    if value is None or not math.isfinite(float(value)):
        return None
    if max_val == min_val:
        return 50.0
    return clamp((float(value) - min_val) / (max_val - min_val) * 100)


def score_higher_better(value: Optional[float], bad: float, good: float) -> Optional[float]:
    return min_max_normalize(value, bad, good)


def score_lower_better(value: Optional[float], good: float, bad: float) -> Optional[float]:
    score = min_max_normalize(value, good, bad)
    return None if score is None else 100.0 - score


def weighted_average(scores: dict[str, Optional[float]], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    present = {name: score for name, score in scores.items() if score is not None}
    if not present:
        return 50.0, {name: 0.0 for name in weights}
    total_weight = sum(weights.get(name, 0.0) for name in present)
    if total_weight <= 0:
        return 50.0, {name: 0.0 for name in weights}
    used = {
        name: (weights[name] / total_weight if name in present else 0.0)
        for name in weights
    }
    total = sum(float(present[name]) * used[name] for name in present)
    return clamp(total), used

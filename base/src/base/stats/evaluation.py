"""Ground-truth assessment tools for any localization plugin."""

from __future__ import annotations

from collections.abc import Sequence

from base.geo_math import haversine_m
from base.schemas import GeoPoint


def geodesic_errors_m(predicted: Sequence[GeoPoint], actual: Sequence[GeoPoint]) -> list[float]:
    if len(predicted) != len(actual):
        raise ValueError("predicted and actual must have equal lengths")
    return [haversine_m(p, a) for p, a in zip(predicted, actual)]


def recall_at_k(ranked_ids: Sequence[Sequence[str]], true_ids: Sequence[str], k: int) -> float:
    """Fraction of queries with ground truth in their top k (identity-based)."""
    if k < 1 or not ranked_ids or len(ranked_ids) != len(true_ids):
        raise ValueError("need nonempty same-length rankings/ground truth, k >= 1")
    return sum(truth in ranking[:k] for ranking, truth in zip(ranked_ids, true_ids))/len(true_ids)


def coverage_at_radius(predicted: Sequence[GeoPoint], actual: Sequence[GeoPoint]) -> float:
    """Fraction of true locations within stated uncertainty radii; missing radii raise."""
    if not predicted or len(predicted) != len(actual):
        raise ValueError("need nonempty, equal-length pairs")
    if any(p.uncertainty_m is None for p in predicted):
        raise ValueError("all predictions require an uncertainty radius to evaluate coverage")
    return sum(haversine_m(p, a) <= p.uncertainty_m for p, a in zip(predicted, actual))/len(predicted)


def expected_calibration_error(probabilities: Sequence[float], correct: Sequence[bool],
                               *, bins: int = 10) -> float:
    """Bin-weighted |accuracy - confidence|; diagnostic, not a calibration algorithm."""
    if not probabilities or len(probabilities) != len(correct) or bins < 1:
        raise ValueError("probabilities/correct must be nonempty, equal length; bins >= 1")
    if any(not 0 <= p <= 1 for p in probabilities):
        raise ValueError("probabilities must be in [0,1]")
    total = len(probabilities)
    result = 0.0
    for idx in range(bins):
        items = [(p, correct[j]) for j, p in enumerate(probabilities)
                 if (min(int(p * bins), bins - 1) == idx)]
        if items:
            result += (len(items)/total) * abs(sum(bool(y) for _, y in items)/len(items) -
                                                sum(p for p, _ in items)/len(items))
    return result

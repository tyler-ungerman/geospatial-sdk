"""Candidate ranking and explicit Bayesian updating (only with justified likelihood ratios)."""

from __future__ import annotations

import math

from base.schemas import GeoCandidate


def aggregate_evidence(candidates: list[GeoCandidate]) -> list[GeoCandidate]:
    """Rank from signed, reliability-weighted evidence; scores are NOT probabilities.

    Scores are fixed-scale 0..1. A confidence field is not fabricated by this function.
    Correlated evidence must be de-duplicated by the caller before ranking.
    """
    ranked = []
    for candidate in candidates:
        weight_sum = sum(e.reliability for e in candidate.evidence)
        signed_support = (sum(e.score * e.reliability for e in candidate.evidence) / weight_sum
                          if weight_sum else 0.0)
        score = max(0.0, min(1.0, (1 + signed_support) / 2))
        ranked.append(candidate.model_copy(update={"rank_score": score}))
    return sorted(ranked, key=lambda item: (-float(item.rank_score or 0), item.id))


def posterior_probability(prior: float, likelihood_ratios: list[float]) -> float:
    """Bayes update for independent (conditional on hypothesis) likelihood ratios.

    Not appropriate for raw heuristic scores, correlated clues or model softmax outputs.
    """
    if not 0 < prior < 1 or any(not math.isfinite(v) or v <= 0 for v in likelihood_ratios):
        raise ValueError("prior must be in (0,1), and likelihood ratios finite and positive")
    log_odds = math.log(prior/(1-prior)) + sum(math.log(v) for v in likelihood_ratios)
    return 1/(1+math.exp(-log_odds)) if log_odds >= 0 else math.exp(log_odds)/(1+math.exp(log_odds))

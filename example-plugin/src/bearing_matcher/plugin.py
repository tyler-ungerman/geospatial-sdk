"""Plugin shows how specialized logic composes base schemas/math/stats."""
from __future__ import annotations

from base.geo_math import initial_bearing_deg, angular_difference_deg
from base.plugins import register_plugin
from base.schemas import EvidenceItem, GeoCandidate, GeoPoint
from base.stats import aggregate_evidence


def match_bearings(candidates: list[GeoPoint], landmark: GeoPoint, *,
                   observed_bearing_deg: float, tolerance_deg: float = 15.0) -> list[GeoCandidate]:
    """Rank hypothesized observer positions from a measured landmark bearing.

    This score is a heuristic, not a calibrated localization probability.
    """
    if not 0 < tolerance_deg <= 180:
        raise ValueError("tolerance_deg must be in (0,180]")
    records = []
    for index, point in enumerate(candidates):
        predicted = initial_bearing_deg(point, landmark)
        difference = abs(angular_difference_deg(predicted, observed_bearing_deg))
        score = max(-1.0, 1 - 2*difference/tolerance_deg)
        records.append(GeoCandidate(id=f"candidate-{index}", location=point,
             evidence=[EvidenceItem(id=f"bearing-{index}", source="user-bearing",
                                    description=f"Predicted bearing {predicted:.2f}; residual {difference:.2f} degrees",
                                    score=score, reliability=1.0, observed=True)]))
    return aggregate_evidence(records)


def setup() -> None:
    register_plugin("bearing_matcher", "Candidate observer locations from measured landmark bearings")(match_bearings)

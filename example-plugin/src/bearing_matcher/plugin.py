"""Plugin shows how specialized logic composes base schemas/math/stats."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from gspace.geo_math import initial_bearing_deg, angular_difference_deg
from gspace.plugins import register_plugin
from gspace.datasources.query import LayerResult
from gspace.schemas import EvidenceItem, GeoCandidate, GeoPoint
from gspace.stats import aggregate_evidence


class RoadLayerSummary(BaseModel):
    """Typed report showing that plugins can return structured results."""

    model_config = ConfigDict(extra="forbid")
    feature_count: int = Field(ge=0)
    coverage_status: Literal["complete", "partial", "unknown", "failed"]
    highway_counts: dict[str, int]


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


def summarize_roads(layer: LayerResult) -> dict:
    """Summarize an SDK road result without changing or ranking its features."""
    counts: dict[str, int] = {}
    for feature in layer.data.get("features", []):
        highway = (feature.get("properties") or {}).get("highway")
        if isinstance(highway, str):
            counts[highway] = counts.get(highway, 0) + 1
    return {"feature_count": len(layer.data.get("features", [])),
            "coverage_status": layer.coverage.status,
            "highway_counts": counts}


def setup() -> None:
    register_plugin("bearing_matcher", "Candidate observer locations from measured landmark bearings")(match_bearings)
    register_plugin("road_layer_summary", "Summarize a typed OSM layer result",
        api_version="1.0.0", input_type=LayerResult, output_type=RoadLayerSummary,
        resource_needs=("osm.roads",))(summarize_roads)

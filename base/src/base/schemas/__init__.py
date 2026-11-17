"""Strict, JSON-serializable contracts that plugins exchange."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LocationBasis = Literal["reported", "inferred", "derived"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GeoPoint(Contract):
    """WGS84 latitude/longitude; uncertainty_m=None means not quantified, not zero."""

    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    uncertainty_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    basis: LocationBasis = "reported"

    def coordinates(self) -> list[float]:
        return [self.lng, self.lat]  # GeoJSON axis order


class BoundingBox(Contract):
    min_lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    min_lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    max_lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    max_lon: float = Field(ge=-180, le=180, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_bounds(self) -> "BoundingBox":
        if self.min_lat >= self.max_lat or self.min_lon >= self.max_lon:
            raise ValueError("BoundingBox must have nonzero extent; antimeridian crossings must be split")
        return self

    def contains(self, point: GeoPoint) -> bool:
        return self.min_lat <= point.lat <= self.max_lat and self.min_lon <= point.lng <= self.max_lon

    def overpass_tuple(self) -> tuple[float, float, float, float]:
        return self.min_lat, self.min_lon, self.max_lat, self.max_lon


class EvidenceItem(Contract):
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    description: str = Field(min_length=1)
    score: float = Field(ge=-1, le=1, allow_inf_nan=False, description="Signed support, not probability")
    reliability: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    observed: bool = True
    url: str | None = None
    recorded_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeoCandidate(Contract):
    id: str = Field(min_length=1)
    location: GeoPoint
    evidence: list[EvidenceItem] = Field(min_length=1)
    rank_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False,
                                     description="Only set from empirical calibration")
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeoEvent(Contract):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    category: str
    severity: str
    location: GeoPoint | None = None
    description: str | None = None
    country: str | None = None
    url: str | None = None
    is_breaking: bool = False
    ingested_at: datetime | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class GeoEntity(Contract):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    location: GeoPoint
    type: str = "unknown"
    updated_at: datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class ElevationGrid(Contract):
    """Regular lat/lng grid; row 0 corresponds to min_lat, column 0 to min_lon."""

    bounds: BoundingBox
    elevations_m: list[list[float | None]]
    dataset: str = "srtm90m"

    @model_validator(mode="after")
    def rectangular(self) -> "ElevationGrid":
        if len(self.elevations_m) < 2 or len(self.elevations_m[0]) < 2:
            raise ValueError("Elevation grid requires at least 2x2 cells")
        if any(len(row) != len(self.elevations_m[0]) for row in self.elevations_m):
            raise ValueError("All elevation grid rows must have the same length")
        return self

    @property
    def rows(self) -> int:
        return len(self.elevations_m)

    @property
    def cols(self) -> int:
        return len(self.elevations_m[0])

    def sample(self, point: GeoPoint) -> float | None:
        """Bilinear interpolation; None if outside the grid or a neighbor is missing."""
        if not self.bounds.contains(point):
            return None
        from math import floor
        y = (point.lat - self.bounds.min_lat) / (self.bounds.max_lat - self.bounds.min_lat) * (self.rows - 1)
        x = (point.lng - self.bounds.min_lon) / (self.bounds.max_lon - self.bounds.min_lon) * (self.cols - 1)
        r, c = min(floor(y), self.rows - 2), min(floor(x), self.cols - 2)
        dy, dx = y - r, x - c
        a, b, d, e = (self.elevations_m[r][c], self.elevations_m[r][c+1],
                      self.elevations_m[r+1][c], self.elevations_m[r+1][c+1])
        if any(v is None for v in (a, b, d, e)):
            return None
        return (a * (1-dx) * (1-dy) + b * dx * (1-dy) + d * (1-dx) * dy + e * dx * dy)


__all__ = ["GeoPoint", "BoundingBox", "EvidenceItem", "GeoCandidate", "GeoEvent", "GeoEntity",
           "ElevationGrid", "LocationBasis"]

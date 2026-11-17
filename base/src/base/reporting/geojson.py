from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from base.schemas import GeoCandidate, GeoEvent, GeoEntity, GeoPoint


def _record_to_feature(record: GeoCandidate | GeoEvent | GeoEntity) -> dict[str, Any] | None:
    point = record.location
    if point is None:
        return None
    attributes = record.model_dump(mode="json", exclude={"location"})
    attributes["uncertainty_m"] = point.uncertainty_m
    attributes["location_basis"] = point.basis
    if isinstance(record, GeoCandidate):
        attributes["evidence_count"] = len(record.evidence)
    return {"type": "Feature", "id": record.id, "geometry": {
        "type": "Point", "coordinates": point.coordinates()}, "properties": attributes}


def feature_collection(records: list[GeoCandidate | GeoEvent | GeoEntity]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [feature for record in records
                                                     if (feature := _record_to_feature(record)) is not None]}


def export_geojson(records: list[GeoCandidate | GeoEvent | GeoEntity], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feature_collection(records), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_json(records: list[BaseModel], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([r.model_dump(mode="json") for r in records], ensure_ascii=False, indent=2), encoding="utf-8")
    return path

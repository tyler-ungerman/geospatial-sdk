"""Exercise a complete local OSM query and deterministic snapshot replay.

Run from a checkout after installing the public package:

    python docs/quickstart_offline.py

The small road extract is generated in a temporary directory. No network call
or external data is used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from gspace.datasources import LayerRequest, SpatialQuery
from gspace.datasources.local_osm import query_local_osm_roads, snapshot_local_osm_extract
from gspace.schemas import BoundingBox


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="geospatial-quickstart-") as work:
        directory = Path(work)
        extract = directory / "roads.geojson"
        manifest_path = directory / "roads.geojson.manifest.json"
        snapshot = directory / "roads.snapshot.zip"

        feature_collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "way-1001",
                    "properties": {"highway": "residential"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-1.1, 51.0], [-0.9, 51.1]],
                    },
                }
            ],
        }
        data = (json.dumps(feature_collection, sort_keys=True, separators=(",", ":")) + "\n").encode()
        extract.write_bytes(data)
        manifest = {
            "source_id": "example.roads",
            "provider": "Quickstart synthetic fixture",
            "content_sha256": sha256(data).hexdigest(),
            "coverage": {
                "min_lat": 50.9,
                "min_lon": -1.2,
                "max_lat": 51.2,
                "max_lon": -0.8,
            },
            "coverage_complete": True,
            "retrieved_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
            "source_version": "synthetic-fixture-v1",
            "licence": "CC0-1.0",
            "attribution": "Synthetic example created by this quickstart",
        }
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

        query = SpatialQuery(
            region=BoundingBox(min_lat=50.95, min_lon=-1.05, max_lat=51.15, max_lon=-0.85),
            layers=[LayerRequest(name="osm.roads", filters={"highway_types": ["residential"]})],
            offline=True,
            require_complete=True,
            cache_policy="read_only",
        )

        first = query_local_osm_roads(query, extract)
        snapshot_local_osm_extract(extract, snapshot)
        replayed = query_local_osm_roads(query, snapshot)

        assert first.coverage.status == replayed.coverage.status == "complete"
        assert first.data == replayed.data
        assert first.provenance[0].content_sha256 == replayed.provenance[0].content_sha256
        assert first.provenance[0].request_digest == replayed.provenance[0].request_digest
        print(f"coverage={replayed.coverage.status}; roads={len(replayed.data['features'])}")
        print(f"source={replayed.provenance[0].source_id}; offline replay matched")


if __name__ == "__main__":
    main()

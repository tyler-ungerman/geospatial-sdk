"""Small-area elevation sampling through OpenTopoData's SRTM90m API.

SRTM90m covers approximately 60°S–60°N. The public endpoint limits requests;
not suitable for bulk DEM ingestion or high-resolution work.
"""

from __future__ import annotations

from urllib.parse import urlencode

from base.schemas import BoundingBox, ElevationGrid, GeoPoint
from .http import FetchError, Transport, http_get, to_number

ELEVATION_URL = "https://api.opentopodata.org/v1/srtm90m"


def fetch_patch(bounds: BoundingBox, *, rows: int = 5, cols: int = 5,
                client: Transport | None = None, endpoint: str = ELEVATION_URL) -> ElevationGrid:
    if rows < 2 or cols < 2 or rows * cols > 100:
        raise ValueError("2x2 through 100 total samples per request supported")
    if bounds.min_lat < -60 or bounds.max_lat > 60:
        raise ValueError("SRTM90m covers approximately 60°S–60°N; choose another dataset for poles")
    points = [GeoPoint(lat=bounds.min_lat + r*(bounds.max_lat-bounds.min_lat)/(rows-1),
                       lng=bounds.min_lon + c*(bounds.max_lon-bounds.min_lon)/(cols-1))
              for r in range(rows) for c in range(cols)]
    locations = "|".join(f"{p.lat:.7f},{p.lng:.7f}" for p in points)
    data = http_get(client, f"{endpoint}?{urlencode({'locations': locations})}", timeout=30).json()
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or len(results) != len(points):
        raise FetchError("OpenTopoData response does not match requested grid")
    elevations = []
    for r in range(rows):
        elevations.append([])
        for item in results[r*cols:(r+1)*cols]:
            if not isinstance(item, dict):
                raise FetchError("Invalid elevation sample")
            elevations[-1].append(to_number(item.get("elevation")))
    return ElevationGrid(bounds=bounds, elevations_m=elevations, dataset="srtm90m")

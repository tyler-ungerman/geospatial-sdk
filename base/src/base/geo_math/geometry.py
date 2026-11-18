from __future__ import annotations

import math

from base.schemas import BoundingBox, GeoPoint

EARTH_RADIUS_M = 6_371_008.8  # mean earth radius; spherical approximation
MERCATOR_RADIUS_M = 6_378_137.0


def haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    """Great-circle distance in meters, spherical Earth (not a survey-grade geodesic)."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat, dlon = lat2 - lat1, math.radians(b.lng - a.lng)
    h = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, h)))


def initial_bearing_deg(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlng = math.radians(b.lng - a.lng)
    y = math.sin(dlng) * math.cos(lat2)
    x = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dlng)
    if abs(x) < 1e-15 and abs(y) < 1e-15:
        return 0.0
    return math.degrees(math.atan2(y, x)) % 360


def angular_difference_deg(a: float, b: float) -> float:
    """Signed smallest angle a-b in [-180, 180)."""
    return (a - b + 180) % 360 - 180


def destination_point(start: GeoPoint, bearing_deg: float, distance_m: float) -> GeoPoint:
    if not math.isfinite(distance_m) or distance_m < 0:
        raise ValueError("distance_m must be finite and >= 0")
    if not math.isfinite(bearing_deg):
        raise ValueError("bearing must be finite")
    phi1, lam1 = map(math.radians, (start.lat, start.lng))
    theta, delta = math.radians(bearing_deg), distance_m / EARTH_RADIUS_M
    phi2 = math.asin(max(-1, min(1, math.sin(phi1)*math.cos(delta)+math.cos(phi1)*math.sin(delta)*math.cos(theta))))
    lam2 = lam1 + math.atan2(math.sin(theta)*math.sin(delta)*math.cos(phi1), math.cos(delta)-math.sin(phi1)*math.sin(phi2))
    return GeoPoint(lat=math.degrees(phi2), lng=((math.degrees(lam2)+180)%360)-180, basis="derived")


def interpolate_great_circle(start: GeoPoint, end: GeoPoint, fraction: float) -> GeoPoint:
    if not 0 <= fraction <= 1:
        raise ValueError("fraction must be in [0, 1]")
    if fraction == 0:
        return start
    if fraction == 1:
        return end
    return destination_point(start, initial_bearing_deg(start, end), haversine_m(start, end)*fraction)


def bbox_from_radius(center: GeoPoint, radius_m: float) -> BoundingBox:
    if not math.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("radius_m must be positive")
    # Conservative local bbox; refuse polar/antimeridian cases rather than produce invalid bounds.
    south, north = destination_point(center, 180, radius_m), destination_point(center, 0, radius_m)
    if south.lat <= -90 or north.lat >= 90:
        raise ValueError("radius reaches pole; split query into smaller bounds")
    cos_lat = math.cos(math.radians(center.lat))
    lon_half_span = math.degrees(radius_m / (EARTH_RADIUS_M*cos_lat)) if cos_lat > 0 else float("inf")
    if lon_half_span >= 180 or center.lng - lon_half_span < -180 or center.lng + lon_half_span > 180:
        raise ValueError("radius crosses antimeridian; split into two BoundingBox values")
    return BoundingBox(min_lat=south.lat, max_lat=north.lat,
                       min_lon=center.lng-lon_half_span, max_lon=center.lng+lon_half_span)


def web_mercator(point: GeoPoint) -> tuple[float, float]:
    if abs(point.lat) > 85.05112878:
        raise ValueError("Web Mercator is undefined beyond +/-85.05112878 degrees")
    return (MERCATOR_RADIUS_M * math.radians(point.lng),
            MERCATOR_RADIUS_M * math.log(math.tan(math.pi/4 + math.radians(point.lat)/2)))


def from_web_mercator(x: float, y: float) -> GeoPoint:
    if not all(map(math.isfinite, (x, y))):
        raise ValueError("Projected coordinates must be finite")
    lng = math.degrees(x / MERCATOR_RADIUS_M)
    lat = math.degrees(2 * math.atan(math.exp(y / MERCATOR_RADIUS_M)) - math.pi/2)
    return GeoPoint(lat=lat, lng=lng, basis="derived")


def raycast_fov_sectors(origin: GeoPoint, bearing_deg: float, features: dict,
                       fov_deg: float = 70.0, max_range_m: float = 180.0) -> dict:
    """Estimate first-hit context along deterministic camera rays.

    Values are fractions of rays whose nearest mapped feature is of a class
    (visibility, not polygon area coverage). Open space is reported only when
    the caller marks the map extract ``coverage_complete=True``; otherwise an
    unhit ray is unknown and does not count as open. Feature geometries crossed
    by several samples count once per ray, preventing geometry overcounting.
    """
    if not math.isfinite(bearing_deg) or not 0 < fov_deg <= 180 or max_range_m <= 0:
        raise ValueError("bearing must be finite, fov in (0,180], and range positive")
    sector_names = ("left", "center", "right")
    classes = ("building", "water", "greenery")
    rays_per_sector = 7
    samples = max(2, int(max_range_m // 5))
    prepared = []
    spatial: dict[tuple[int, int], list[int]] = {}
    cell_size = 20.0
    for idx, feature in enumerate(features.get("features", []) if isinstance(features, dict) else []):
        geom = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        category = _context_category(props)
        if category is None:
            continue
        kind = geom.get("type", "")
        raw = geom.get("coordinates", [])
        if kind == "Polygon":
            groups = [[_flatten_geometry(ring) for ring in raw]]
        elif kind == "MultiPolygon":
            groups = [[_flatten_geometry(ring) for ring in polygon] for polygon in raw]
        elif kind == "MultiLineString":
            groups = [[_flatten_geometry(line)] for line in raw]
        else:
            points = _flatten_geometry(raw)
            groups = [[points]] if points else []
        projected = [[[((math.radians(lon-origin.lng)*EARTH_RADIUS_M*math.cos(math.radians(origin.lat))),
                        math.radians(lat-origin.lat)*EARTH_RADIUS_M) for lon, lat in ring]
                      for ring in group if ring] for group in groups if group]
        if projected:
            flat = [point for group in projected for ring in group for point in ring]
            bounds_xy = (min(p[0] for p in flat)-8, min(p[1] for p in flat)-8,
                         max(p[0] for p in flat)+8, max(p[1] for p in flat)+8)
            prepared.append((str(feature.get("id", idx)), category, kind, projected, bounds_xy))
            item_index = len(prepared)-1
            cell_x0 = max(-int(max_range_m/cell_size)-1, math.floor(bounds_xy[0]/cell_size))
            cell_x1 = min(int(max_range_m/cell_size)+1, math.floor(bounds_xy[2]/cell_size))
            cell_y0 = max(-int(max_range_m/cell_size)-1, math.floor(bounds_xy[1]/cell_size))
            cell_y1 = min(int(max_range_m/cell_size)+1, math.floor(bounds_xy[3]/cell_size))
            if cell_x1-cell_x0 <= 40 and cell_y1-cell_y0 <= 40:
                for cell_x in range(cell_x0, cell_x1+1):
                    for cell_y in range(cell_y0, cell_y1+1):
                        spatial.setdefault((cell_x, cell_y), []).append(item_index)

    result = {}
    for sector_index, sector in enumerate(sector_names):
        hits = {name: 0 for name in classes}
        open_hits = 0
        for ray_index in range(rays_per_sector):
            # Three equal sectors, with rays spread uniformly over each sector.
            fraction = (ray_index + 0.5) / rays_per_sector
            angle = bearing_deg - fov_deg/2 + (sector_index + fraction) * (fov_deg/3)
            observed = False
            for step in range(1, samples+1):
                distance = max_range_m * step / samples
                point = destination_point(origin, angle, distance)
                x = math.radians(point.lng-origin.lng)*EARTH_RADIUS_M*math.cos(math.radians(origin.lat))
                y = math.radians(point.lat-origin.lat)*EARTH_RADIUS_M
                for item_index in spatial.get((math.floor(x/cell_size), math.floor(y/cell_size)), ()):
                    _, category, kind, coordinates, bounds_xy = prepared[item_index]
                    if _geometry_hit(x, y, coordinates, kind):
                        hits[category] += 1
                        observed = True
                        break
                if observed:
                    break
            if not observed and features.get("coverage_complete") is True:
                open_hits += 1
        result[sector] = {name: hits[name]/rays_per_sector for name in classes}
        result[sector]["open"] = open_hits/rays_per_sector
        result[sector]["known_fraction"] = (sum(hits.values())+open_hits)/rays_per_sector
    return result


def _context_category(tags: dict) -> str | None:
    if "building" in tags and tags.get("building") not in (None, "no"):
        return "building"
    if tags.get("natural") in {"water", "coastline"} or "waterway" in tags or tags.get("landuse") == "reservoir":
        return "water"
    if tags.get("natural") in {"wood", "scrub", "grassland", "heath", "tree_row"} or tags.get("landuse") in {
            "forest", "grass", "meadow", "farmland", "orchard", "vineyard", "recreation_ground"} or tags.get("leisure") == "park":
        return "greenery"
    return None


def _flatten_geometry(coordinates):
    if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2 and all(isinstance(v, (int, float)) for v in coordinates[:2]):
        return [(float(coordinates[0]), float(coordinates[1]))]
    output = []
    if isinstance(coordinates, (list, tuple)):
        for child in coordinates:
            output.extend(_flatten_geometry(child))
    return output


def _geometry_hit(x: float, y: float, groups: list[list[list[tuple[float, float]]]], kind: str) -> bool:
    # Project coordinates into the same local equirectangular metric frame.
    # A small tolerance approximates road/coastline line width; polygons use
    # point-in-ring inclusion.
    if kind in {"Polygon", "MultiPolygon"}:
        def in_ring(points):
            inside = False
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                if ((y1 > y) != (y2 > y)) and x < (x2-x1)*(y-y1)/(y2-y1+1e-30)+x1:
                    inside = not inside
            return inside
        for rings in groups:
            if not rings or len(rings[0]) < 4 or not in_ring(rings[0]):
                continue
            if not any(len(hole) >= 4 and in_ring(hole) for hole in rings[1:]):
                return True
        return False
    tolerance = 8.0
    for group in groups:
        for points in group:
            for (ax, ay), (bx, by) in zip(points, points[1:]):
                px, py = x, y
                dx, dy = bx-ax, by-ay
                denom = dx*dx+dy*dy
                t = 0.0 if denom == 0 else max(0.0, min(1.0, ((px-ax)*dx+(py-ay)*dy)/denom))
                if math.hypot(px-(ax+t*dx), py-(ay+t*dy)) <= tolerance:
                    return True
    return False

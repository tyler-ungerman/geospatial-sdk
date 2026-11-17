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

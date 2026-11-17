"""Terrain-only visibility, independent of buildings/trees/weather/atmospheric refraction."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from base.geo_math.geometry import haversine_m, interpolate_great_circle
from base.schemas import GeoPoint


def line_of_sight(observer: GeoPoint, target: GeoPoint, elevation_at: Callable[[GeoPoint], float | None],
                  *, observer_height_m: float = 1.7, target_height_m: float = 1.7,
                  samples: int = 64, earth_curvature: bool = True) -> bool | None:
    """True/False for clear/blocked terrain ray; None if DEM samples missing.

    Uses a spherical-earth curvature correction, without refraction. Heights are above sampled terrain.
    """
    if samples < 2 or observer_height_m < 0 or target_height_m < 0:
        raise ValueError("Need >=2 samples and nonnegative heights")
    a, b = elevation_at(observer), elevation_at(target)
    if a is None or b is None:
        return None
    from .geometry import EARTH_RADIUS_M
    distance = haversine_m(observer, target)
    for i in range(1, samples):
        f = i / samples
        ground = elevation_at(interpolate_great_circle(observer, target, f))
        if ground is None:
            return None
        ray_height = (a + observer_height_m)*(1-f) + (b + target_height_m)*f
        curvature_bulge = distance**2 * f*(1-f) / (2 * EARTH_RADIUS_M) if earth_curvature else 0
        if ground + curvature_bulge > ray_height:
            return False
    return True


def horizon_profile(observer: GeoPoint, paths: Sequence[Sequence[GeoPoint]],
                    elevation_at: Callable[[GeoPoint], float | None], *, observer_height_m: float = 1.7,
                    earth_curvature: bool = True) -> list[tuple[float, float | None]]:
    """(bearing degrees, maximum terrain elevation angle) for each radial sample path."""
    from .geometry import EARTH_RADIUS_M, initial_bearing_deg
    base = elevation_at(observer)
    result = []
    for path in paths:
        if not path:
            continue
        angle: float | None = None
        if base is not None:
            for p in path:
                d = haversine_m(observer, p)
                elev = elevation_at(p)
                if elev is None or d == 0:
                    continue
                drop = d*d/(2*EARTH_RADIUS_M) if earth_curvature else 0
                a = math.degrees(math.atan2(elev - drop - base - observer_height_m, d))
                angle = max(angle, a) if angle is not None else a
        result.append((initial_bearing_deg(observer, path[-1]), angle))
    return result

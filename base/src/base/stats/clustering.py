"""Radius-based density clustering of small point sets; O(n²), no sklearn dependency."""

from __future__ import annotations

from base.geo_math import haversine_m
from base.schemas import GeoPoint


def cluster_points(points: list[GeoPoint], *, eps_m: float, min_samples: int = 2) -> list[int]:
    """DBSCAN-compatible cluster labels (0..n, -1 for noise); deterministic input order.

    Distance is geographical great-circle distance; appropriate for small/moderate collections.
    """
    if eps_m <= 0 or min_samples < 1:
        raise ValueError("eps_m > 0 and min_samples >= 1 required")
    n = len(points)
    neighbors = [[j for j in range(n) if haversine_m(points[i], points[j]) <= eps_m] for i in range(n)]
    UNSEEN = -2
    labels = [UNSEEN] * n
    cluster = 0
    for idx in range(n):
        if labels[idx] != UNSEEN:
            continue
        if len(neighbors[idx]) < min_samples:
            labels[idx] = -1
            continue
        labels[idx] = cluster
        seeds = list(neighbors[idx])
        included = set(seeds)
        k = 0
        while k < len(seeds):
            point_idx = seeds[k]
            k += 1
            if labels[point_idx] == -1:
                labels[point_idx] = cluster
            if labels[point_idx] != UNSEEN:
                continue
            labels[point_idx] = cluster
            if len(neighbors[point_idx]) >= min_samples:
                for neighbor in neighbors[point_idx]:
                    if neighbor not in included:
                        included.add(neighbor)
                        seeds.append(neighbor)
        cluster += 1
    return labels

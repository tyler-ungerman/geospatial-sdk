"""Normalized Difference Vegetation Index: (NIR - red)/(NIR + red)."""
from __future__ import annotations
import math

def ndvi(nir: float, red: float) -> float | None:
    if not all(map(math.isfinite, (nir, red))) or nir < 0 or red < 0:
        raise ValueError("Reflectance inputs must be finite and nonnegative")
    total = nir + red
    return (nir - red) / total if total else None

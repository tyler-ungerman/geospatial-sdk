"""Normalized Difference Moisture Index: (NIR - SWIR)/(NIR + SWIR)."""
from __future__ import annotations
import math

def ndmi(nir: float, swir: float) -> float | None:
    if not all(map(math.isfinite, (nir, swir))) or nir < 0 or swir < 0:
        raise ValueError("Reflectance inputs must be finite and nonnegative")
    total = nir + swir
    return (nir - swir) / total if total else None

"""Simple empirical percentile interpolation, matching common type-7 quantiles."""
from __future__ import annotations
import math

def percentile(data: list[float], p: float) -> float:
    if not data or not 0 <= p <= 100 or any(not math.isfinite(x) for x in data):
        raise ValueError("Nonempty finite data and percentile in [0,100] required")
    values = sorted(data)
    pos = (len(values)-1) * p/100
    i = int(pos)
    return values[i] + (values[min(i+1, len(values)-1)] - values[i])*(pos-i)

"""Spatial grouping, ranking and evaluation without opaque confidence claims."""
from .clustering import cluster_points
from .ranking import aggregate_evidence, posterior_probability
from .evaluation import geodesic_errors_m, recall_at_k, coverage_at_radius, expected_calibration_error

__all__ = ["cluster_points", "aggregate_evidence", "posterior_probability",
           "geodesic_errors_m", "recall_at_k", "coverage_at_radius", "expected_calibration_error"]

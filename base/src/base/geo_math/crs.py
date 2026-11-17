"""Optional arbitrary CRS transformations via pyproj, always x/y axis order."""

from __future__ import annotations


def transform_xy(x: float, y: float, *, from_crs: str, to_crs: str) -> tuple[float, float]:
    """Transform (x,y), e.g. lon/lat EPSG:4326 to meters EPSG:27700.

    Install `geospatial-base-sdk[all]` for pyproj. Always returns x,y in the
    specified target projection; does not represent projected meters as GeoPoint.
    """
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError("Install geospatial-base-sdk[all] for arbitrary CRS transformations") from exc
    transformer = Transformer.from_crs(from_crs, to_crs, always_xy=True)
    transformed_x, transformed_y = transformer.transform(x, y)
    return float(transformed_x), float(transformed_y)

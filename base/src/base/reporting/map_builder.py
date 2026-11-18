"""HTML map export is opt-in (pip install gspace-sdk[map])."""

from __future__ import annotations

from pathlib import Path

from base.schemas import GeoCandidate, GeoEntity, GeoEvent


def export_html(records: list[GeoCandidate | GeoEntity | GeoEvent], output_path: str | Path) -> Path:
    try:
        import folium
    except ImportError as exc:
        raise RuntimeError("Install gspace-sdk[map] for Folium map export") from exc
    points = [r for r in records if r.location is not None]
    center = ([sum(r.location.lat for r in points)/len(points),
               sum(r.location.lng for r in points)/len(points)] if points else [0.0, 0.0])
    m = folium.Map(location=center, zoom_start=6 if points else 2, control_scale=True)
    for record in points:
        point = record.location
        label = getattr(record, "title", getattr(record, "name", record.id))
        popup = folium.Popup(folium.Html(
            __import__("html").escape(str(label)) + "<hr>Uncertainty: " +
            (f"{point.uncertainty_m:.0f} m" if point.uncertainty_m is not None else "not quantified") +
            "<hr>Location: " + __import__("html").escape(point.basis), script=False), max_width=350)
        folium.Marker([point.lat, point.lng], popup=popup).add_to(m)
        if point.uncertainty_m and point.uncertainty_m > 0:
            folium.Circle(location=[point.lat, point.lng], radius=point.uncertainty_m,
                          color="gray", fill=False).add_to(m)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(path))
    return path

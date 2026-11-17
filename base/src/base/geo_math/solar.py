"""Approximate solar position via the NOAA solar-position equations.

Suitable for coarse photo-shadow bearings, not precise astronomical navigation.
No atmospheric refraction or local horizon model is applied.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from base.schemas import GeoPoint


def sun_position(point: GeoPoint, when: datetime) -> tuple[float, float]:
    """Return (azimuth from true north clockwise, elevation above geometric horizon), degrees."""
    if when.tzinfo is None:
        raise ValueError("datetime must be timezone aware")
    moment = when.astimezone(timezone.utc)
    jd = moment.timestamp()/86400 + 2440587.5
    t = (jd - 2451545.0) / 36525
    rad = math.radians
    mean_long = (280.46646 + t*(36000.76983 + t*.0003032)) % 360
    mean_anomaly = (357.52911 + t*(35999.05029 - .0001537*t)) % 360
    eccentricity = .016708634 - t*(.000042037 + .0000001267*t)
    center = (math.sin(rad(mean_anomaly))*(1.914602-t*(.004817+.000014*t)) +
              math.sin(rad(2*mean_anomaly))*(.019993-.000101*t) +
              math.sin(rad(3*mean_anomaly))*.000289)
    omega = 125.04 - 1934.136*t
    solar_long = mean_long + center - .00569 - .00478*math.sin(rad(omega))
    mean_obliq = 23 + (26 + (21.448-t*(46.815+t*(.00059-t*.001813)))/60)/60
    obliq = mean_obliq + .00256*math.cos(rad(omega))
    decl = math.asin(math.sin(rad(obliq))*math.sin(rad(solar_long)))
    y = math.tan(rad(obliq)/2)**2
    eq_time = 4*math.degrees(
        y*math.sin(rad(2*mean_long)) - 2*eccentricity*math.sin(rad(mean_anomaly)) +
        4*eccentricity*y*math.sin(rad(mean_anomaly))*math.cos(rad(2*mean_long)) -
        .5*y*y*math.sin(rad(4*mean_long)) -
        1.25*eccentricity**2*math.sin(rad(2*mean_anomaly)))
    minutes = (moment.hour*60 + moment.minute + moment.second/60 + moment.microsecond/60_000_000)
    true_solar = (minutes + eq_time + 4*point.lng) % 1440
    hour_angle = rad(true_solar/4-180)
    phi = rad(point.lat)
    cos_zenith = math.sin(phi)*math.sin(decl) + math.cos(phi)*math.cos(decl)*math.cos(hour_angle)
    zenith = math.acos(max(-1, min(1, cos_zenith)))
    elevation = 90 - math.degrees(zenith)
    azimuth = (math.degrees(math.atan2(math.sin(hour_angle),
                       math.cos(hour_angle)*math.sin(phi)-math.tan(decl)*math.cos(phi)))+180) % 360
    return azimuth, elevation

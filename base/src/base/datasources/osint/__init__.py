"""Bundled OSINT collectors; importing this module registers them."""

from .usgs import fetch_earthquakes
from .firms import fetch_firms
from .gdacs import fetch_gdacs
from .opensky import fetch_opensky
from .gdelt import fetch_osint_news
from .google_news import fetch_google_news
from .classification import classify_event

__all__ = ["fetch_earthquakes", "fetch_firms", "fetch_gdacs", "fetch_opensky",
           "fetch_osint_news", "fetch_google_news", "classify_event"]

"""Google News RSS source with optional AI enrichment."""

from __future__ import annotations

import html
import logging
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from base.datasources.http import FetchError, JsonRecord, SourceUnavailable, Transport, http_get
from .classification import classified_fields, deduplicate, optional_classifications
from base.datasources.registry import register_source

logger = logging.getLogger(__name__)

GNEWS_URL = "https://news.google.com/rss/search"
NEWS_TOPICS: tuple[tuple[str, str], ...] = (
    ("military conflict war", "military"),
    ("economy sanctions recession", "economy"),
    ("trade tariff shipping", "trade"),
    ("pandemic disease outbreak", "health"),
    ("political crisis election coup", "political"),
)


def _parse_rss_items(xml: str) -> list[JsonRecord]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise FetchError("Malformed RSS XML") from exc
    articles: list[JsonRecord] = []
    for item in root.findall(".//item"):
        title = html.unescape((item.findtext("title") or "").strip())
        if not title or "Google News" in title:
            continue
        articles.append({
            "title": title, "link": (item.findtext("link") or "").strip(),
            "pubDate": (item.findtext("pubDate") or "").strip(),
            "source": html.unescape((item.findtext("source") or "Google News").strip()),
        })
    return articles


@register_source("google_news", kind="events")
def fetch_google_news(
    *, client: Transport | None = None, ai_api_key: str | None = None,
    per_topic: int = 3, inter_feed_delay: float = 1.0,
) -> list[JsonRecord]:
    """Google News RSS across the five original topics; AI enrichment is optional."""
    articles: list[JsonRecord] = []
    successful_feeds = 0
    for index, (query, category) in enumerate(NEWS_TOPICS):
        if index and inter_feed_delay > 0:
            time.sleep(inter_feed_delay)
        url = f"{GNEWS_URL}?{urlencode({'q': query, 'hl': 'en', 'gl': 'US', 'ceid': 'US:en'})}"
        try:
            response = http_get(client, url, headers={"User-Agent": "Mozilla/5.0 OSINT Feed Reader"})
            parsed = _parse_rss_items(response.text)
            successful_feeds += 1
            articles.extend({**article, "queryCategory": category} for article in parsed[:per_topic])
        except FetchError as exc:
            logger.warning("Google News %s fetch failed: %s", category, exc)
    if not successful_feeds:
        raise SourceUnavailable("All Google News topic feeds failed")
    classifications = optional_classifications(articles, client=client, api_key=ai_api_key)
    events: list[JsonRecord] = []
    for index, article in enumerate(articles):
        fields = classified_fields(classifications.get(index), article["queryCategory"])
        events.append({
            **fields, "title": article["title"],
            "source": f"GNews/{article['source']}", "url": article["link"],
            "raw_data": {"source": article["source"], "pubDate": article["pubDate"]},
        })
    return deduplicate(events)
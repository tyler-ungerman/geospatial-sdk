"""GDELT 2.0 document API source with optional AI enrichment."""

from __future__ import annotations

from urllib.parse import urlencode

from base.datasources.http import FetchError, JsonRecord, SourceUnavailable, Transport, http_get
from .classification import classified_fields, deduplicate, extract_json, optional_classifications
from base.datasources.registry import register_source

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


@register_source("gdelt", kind="events")
def fetch_osint_news(
    *, client: Transport | None = None, ai_api_key: str | None = None, limit: int = 15,
) -> list[JsonRecord]:
    """GDELT 2.0 document API, last 24 hours; raise on throttled/non-JSON responses."""
    query = "military OR conflict OR economy OR sanctions OR trade OR pandemic OR health OR political OR crisis"
    url = f"{GDELT_URL}?{urlencode({'query': query, 'mode': 'artlist', 'maxrecords': limit, 'format': 'json', 'sort': 'DateDesc', 'timespan': '24h'})}"
    response = http_get(client, url, timeout=20)
    try:
        data = extract_json(response.text)
    except FetchError as exc:
        raise SourceUnavailable("GDELT returned a non-JSON response (possible rate limit)") from exc
    if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
        raise SourceUnavailable("GDELT response has no articles array")
    articles = [a for a in data["articles"] if isinstance(a, dict) and a.get("title") and a.get("url")][:limit]
    if not articles:
        raise SourceUnavailable("GDELT returned no articles; retaining cached records")
    classifications = optional_classifications(articles, client=client, api_key=ai_api_key)
    events: list[JsonRecord] = []
    for index, article in enumerate(articles):
        fields = classified_fields(classifications.get(index), "political")
        events.append({
            **fields, "title": article["title"],
            "source": f"GDELT/{article.get('domain') or 'Unknown'}", "url": article["url"],
            "raw_data": {"domain": article.get("domain"), "seendate": article.get("seendate")},
        })
    return deduplicate(events)
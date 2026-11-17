"""Optional AI classification and record normalization shared by news-style sources."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from base.datasources.http import FetchError, HttpClient, JsonRecord, SourceUnavailable, Transport, coordinates

logger = logging.getLogger(__name__)

AI_URL = "https://ai.gateway.lovable.dev/v1/chat/completions"
CATEGORIES = frozenset({"military", "economy", "trade", "health", "disaster", "political"})
SEVERITIES = frozenset({"critical", "high", "medium", "low"})

CLASSIFICATION_PROMPT = """You are an OSINT news classifier. For each headline, output a JSON array where each element has:
- index (number)
- category: one of "military","economy","trade","health","disaster","political"
- severity: one of "critical","high","medium","low"
- country: primary country name or null
- lat: approximate latitude or null
- lng: approximate longitude or null
- summary: one short sentence summary
- is_breaking: boolean (true only for major global events)
Respond ONLY with the JSON array, no markdown."""


def extract_json(raw: str) -> Any:
    """Parse plain/fenced model JSON, tolerating non-JSON prefix/suffix text."""
    raw = re.sub(r"```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", raw):
        try:
            value, _ = decoder.raw_decode(raw[match.start():])
            return value
        except json.JSONDecodeError:
            continue
    raise FetchError("AI response contains no valid JSON")


def classify_batch(
    articles: list[JsonRecord], *, client: Transport | None, api_key: str,
) -> dict[int, JsonRecord]:
    if not articles:
        return {}
    request = {
        "model": "google/gemini-2.5-flash-lite",
        "messages": [
            {"role": "system", "content": CLASSIFICATION_PROMPT},
            {"role": "user", "content": "\n".join(f'[{i}] "{article["title"]}"' for i, article in enumerate(articles))},
        ],
    }
    response = (client or HttpClient()).request("POST", AI_URL, headers={"Authorization": f"Bearer {api_key}"}, json_body=request, timeout=30)
    if not 200 <= response.status < 300:
        raise SourceUnavailable(f"AI gateway HTTP {response.status}")
    raw = response.json()
    choices = raw.get("choices") or []
    content = choices[0].get("message", {}).get("content", "") if choices else ""
    parsed = extract_json(content)
    if not isinstance(parsed, list):
        raise FetchError("AI classification response must be an array")
    result: dict[int, JsonRecord] = {}
    for item in parsed:
        if not isinstance(item, dict) or type(item.get("index")) is not int:
            continue
        idx = item["index"]
        if 0 <= idx < len(articles) and idx not in result:
            result[idx] = item
    return result


def optional_classifications(
    articles: list[JsonRecord], *, client: Transport | None, api_key: str | None,
) -> dict[int, JsonRecord]:
    """Classify when an api_key is given; on any AI failure fall back to source categories."""
    if not api_key or not articles:
        return {}
    try:
        return classify_batch(articles, client=client, api_key=api_key)
    except (FetchError, ValueError, KeyError, TypeError) as exc:
        logger.warning("AI classification unavailable; using source categories: %s", exc)
        return {}


def deduplicate(events: list[JsonRecord]) -> list[JsonRecord]:
    unique: list[JsonRecord] = []
    seen: set[str] = set()
    for event in events:
        key = event["title"].strip().casefold()
        if key not in seen:
            seen.add(key)
            unique.append(event)
    return unique


def classified_fields(item: JsonRecord | None, default_category: str) -> JsonRecord:
    """Validated category/severity/location fields from an AI item (or safe defaults)."""
    item = item or {}
    loc = coordinates([item.get("lng"), item.get("lat")])
    category, severity = item.get("category"), item.get("severity")
    return {
        "category": category if isinstance(category, str) and category in CATEGORIES else default_category,
        "severity": severity if isinstance(severity, str) and severity in SEVERITIES else "medium",
        "description": item.get("summary") if isinstance(item.get("summary"), str) else None,
        "country": item.get("country") or None,
        "lat": loc[0] if loc else None,
        "lng": loc[1] if loc else None,
        "is_breaking": item.get("is_breaking") is True,
        "location_basis": "inferred" if loc else None,
        "location_uncertainty_m": None,  # LLM-geocoded location: no empirically measured accuracy
    }


def classify_event(
    headline: str, description: str | None = None, *,
    api_key: str, client: Transport | None = None,
) -> JsonRecord:
    """Port of the standalone ``classify-event`` Edge Function; requires an AI key."""
    if not headline.strip():
        raise ValueError("headline is required")
    if not api_key:
        raise ValueError("api_key is required")
    params = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": sorted(CATEGORIES)},
            "severity": {"type": "string", "enum": sorted(SEVERITIES)},
            "country": {"type": "string"}, "lat": {"type": "number"},
            "lng": {"type": "number"}, "summary": {"type": "string"},
        },
        "required": ["category", "severity", "country", "lat", "lng", "summary"],
        "additionalProperties": False,
    }
    body = {
        "model": "google/gemini-3-flash-preview",
        "messages": [
            {"role": "system", "content": "You are an OSINT event classifier. Classify the given news headline into exactly one category and assess severity. You MUST respond using the classify_event tool."},
            {"role": "user", "content": f"Headline: {headline}\n" + (f"Details: {description}" if description else "")},
        ],
        "tools": [{"type": "function", "function": {"name": "classify_event", "description": "Classify a news event into a category with severity and location", "parameters": params}}],
        "tool_choice": {"type": "function", "function": {"name": "classify_event"}},
    }
    response = (client or HttpClient()).request("POST", AI_URL, headers={"Authorization": f"Bearer {api_key}"}, json_body=body, timeout=30)
    if not 200 <= response.status < 300:
        raise SourceUnavailable(f"AI gateway HTTP {response.status}")
    payload = response.json()
    try:
        args = payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        classification = json.loads(args)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise FetchError("AI gateway response has no valid classify_event tool call") from exc
    if not isinstance(classification, dict):
        raise FetchError("Invalid classify_event result")
    clean = classified_fields(classification, "political")
    return {"category": clean["category"], "severity": clean["severity"], "country": clean["country"], "lat": clean["lat"], "lng": clean["lng"], "summary": classification.get("summary"),
            "location_basis": clean["location_basis"], "location_uncertainty_m": clean["location_uncertainty_m"]}

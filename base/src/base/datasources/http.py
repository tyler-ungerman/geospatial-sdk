"""Standard-library bounded HTTP transport with explicit errors and pluggable caching."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, parse_qsl

JsonRecord = dict[str, Any]


class FetchError(RuntimeError):
    """Source data is unavailable or could not be parsed."""


class SourceUnavailable(FetchError):
    """Source failed transiently; callers may fall back to cached data."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8-sig", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except (ValueError, TypeError) as exc:
            raise FetchError("Invalid JSON returned by source") from exc


class Transport(Protocol):
    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                json_body: JsonRecord | None = None, timeout: float = 15.0) -> HttpResponse: ...


class HttpClient:
    def __init__(self, *, max_response_bytes: int = 20_000_000,
                 user_agent: str = "GeospatialBaseSDK/0.1 (+https://openstreetmap.org)"):
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.max_response_bytes = max_response_bytes
        self.user_agent = user_agent

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                json_body: JsonRecord | None = None, timeout: float = 15.0) -> HttpResponse:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        req_headers = {"User-Agent": self.user_agent, **(headers or {})}
        body = None if json_body is None else json.dumps(json_body).encode("utf-8")
        if body is not None:
            req_headers.setdefault("Content-Type", "application/json")
        request = Request(url, data=body, method=method, headers=req_headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                data = response.read(self.max_response_bytes + 1)
                if len(data) > self.max_response_bytes:
                    raise FetchError("HTTP response exceeded configured size limit")
                return HttpResponse(response.status, data)
        except HTTPError as exc:
            return HttpResponse(exc.code, exc.read(min(self.max_response_bytes, 8192)))
        except (URLError, TimeoutError, OSError) as exc:
            raise SourceUnavailable(f"HTTP transport failed: {type(exc).__name__}: {exc}") from exc


class CachedTransport:
    """Opt-in file-backed TTL cache for anonymous GETs (never caches authorization)."""

    def __init__(self, upstream: Transport, directory: str | Path, *, ttl_seconds: float = 3600):
        if ttl_seconds < 0:
            raise ValueError("TTL must be nonnegative")
        self.upstream = upstream
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                json_body: JsonRecord | None = None, timeout: float = 15.0) -> HttpResponse:
        normalized_headers = {k.lower(): v for k, v in (headers or {}).items()}
        sensitive = {"token", "key", "api_key", "access_key", "secret", "password"}
        query_names = {k.casefold() for k, _ in parse_qsl(urlsplit(url).query)}
        if method.upper() != "GET" or json_body is not None or "authorization" in normalized_headers or query_names & sensitive:
            return self.upstream.request(method, url, headers=headers, json_body=json_body, timeout=timeout)
        key = hashlib.sha256(json.dumps([url, sorted(normalized_headers.items())]).encode()).hexdigest()
        path = self.directory / f"{key}.json"
        if path.exists() and time.time() - path.stat().st_mtime < self.ttl_seconds:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return HttpResponse(status=data["status"], body=bytes.fromhex(data["body_hex"]))
            except (OSError, ValueError, KeyError):
                pass
        response = self.upstream.request(method, url, headers=headers, json_body=json_body, timeout=timeout)
        if 200 <= response.status < 300:
            self.directory.mkdir(parents=True, exist_ok=True)
            import os
            import tempfile
            fd, filename = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"status": response.status, "body_hex": response.body.hex()}, f)
                os.replace(filename, path)
            finally:
                if os.path.exists(filename):
                    os.unlink(filename)
        return response


class RetryingTransport:
    """Retry safe GETs after transient HTTP errors or transport failure.

    Sleeps are bounded; mutating requests are never retried automatically.
    """

    def __init__(self, upstream: Transport, *, attempts: int = 3, backoff_seconds: float = .5,
                 sleep: Any = time.sleep):
        if not 1 <= attempts <= 10 or backoff_seconds < 0:
            raise ValueError("1..10 attempts and nonnegative backoff required")
        self.upstream, self.attempts, self.backoff_seconds, self.sleep = (
            upstream, attempts, backoff_seconds, sleep)

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                json_body: JsonRecord | None = None, timeout: float = 15.0) -> HttpResponse:
        is_safe = method.upper() == "GET" and json_body is None
        attempts = self.attempts if is_safe else 1
        for i in range(attempts):
            try:
                response = self.upstream.request(method, url, headers=headers,
                                                 json_body=json_body, timeout=timeout)
            except SourceUnavailable:
                if i + 1 == attempts:
                    raise
            else:
                if response.status not in (429, 500, 502, 503, 504) or i + 1 == attempts:
                    return response
            self.sleep(self.backoff_seconds * (2**i))
        raise RuntimeError("unreachable")


def http_get(client: Transport | None, url: str, *, headers: Mapping[str, str] | None = None,
             timeout: float = 15.0) -> HttpResponse:
    response = (client or HttpClient()).request("GET", url, headers=headers, timeout=timeout)
    if not 200 <= response.status < 300:
        raise SourceUnavailable(f"HTTP {response.status} from {url}: {response.text[:160]}")
    return response


def to_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def coordinates(coords: Any) -> tuple[float, float] | None:
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    lng, lat = to_number(coords[0]), to_number(coords[1])
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def utc_iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

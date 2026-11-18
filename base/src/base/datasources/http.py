"""Standard-library bounded HTTP transport with explicit errors and pluggable caching."""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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


class CircuitOpen(SourceUnavailable):
    """An endpoint is in cooldown and should fail over without another retry."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] | None = None

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
                return HttpResponse(response.status, data, dict(response.headers.items()))
        except HTTPError as exc:
            return HttpResponse(exc.code, exc.read(min(self.max_response_bytes, 8192)), dict(exc.headers.items()))
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
                 sleep: Any = time.sleep, jitter: Any = random.uniform,
                 max_retry_after_seconds: float = 30.0):
        if not 1 <= attempts <= 10 or backoff_seconds < 0 or max_retry_after_seconds < 0:
            raise ValueError("1..10 attempts and nonnegative backoff required")
        self.upstream, self.attempts, self.backoff_seconds, self.sleep = upstream, attempts, backoff_seconds, sleep
        self.jitter, self.max_retry_after_seconds = jitter, max_retry_after_seconds
        import threading
        self._metrics_lock = threading.Lock()
        self.metrics = {"requests": 0, "retries": 0, "http_429": 0}

    def metrics_snapshot(self, *, reset: bool = False) -> dict[str, int]:
        with self._metrics_lock:
            result = dict(self.metrics)
            if reset:
                for key in self.metrics:
                    self.metrics[key] = 0
            return result

    def _retry_delay(self, response: HttpResponse | None, attempt: int) -> float:
        if response is not None and response.status == 429:
            headers = {str(k).lower(): str(v) for k, v in (response.headers or {}).items()}
            value = headers.get("retry-after")
            if value:
                try:
                    delay = float(value)
                except ValueError:
                    try:
                        when = parsedate_to_datetime(value)
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=timezone.utc)
                        delay = (when - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        delay = -1
                if delay >= 0:
                    return min(self.max_retry_after_seconds, delay)
        ceiling = min(self.max_retry_after_seconds, self.backoff_seconds * (2**attempt))
        return self.jitter(ceiling * 0.5, ceiling) if ceiling > 0 else 0.0

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                json_body: JsonRecord | None = None, timeout: float = 15.0) -> HttpResponse:
        is_safe = method.upper() == "GET" and json_body is None
        attempts = self.attempts if is_safe else 1
        for i in range(attempts):
            response = None
            with self._metrics_lock:
                self.metrics["requests"] += 1
            try:
                response = self.upstream.request(method, url, headers=headers,
                                                 json_body=json_body, timeout=timeout)
            except CircuitOpen:
                raise
            except SourceUnavailable:
                if i + 1 == attempts:
                    raise
            else:
                if response.status == 429:
                    with self._metrics_lock:
                        self.metrics["http_429"] += 1
                if response.status not in (429, 500, 502, 503, 504) or i + 1 == attempts:
                    return response
            with self._metrics_lock:
                self.metrics["retries"] += 1
            self.sleep(self._retry_delay(response, i))
        raise RuntimeError("unreachable")


class RateLimitedTransport:
    """Serialize live requests with a minimum interval between calls."""

    def __init__(self, upstream: Transport, min_interval_seconds: float = 1.0,
                 *, sleep: Any = time.sleep, clock: Any = time.monotonic,
                 max_concurrency: int = 2):
        if min_interval_seconds < 0 or max_concurrency < 1:
            raise ValueError("min_interval_seconds must be nonnegative and concurrency >= 1")
        import threading
        self.upstream = upstream
        self.min_interval_seconds = min_interval_seconds
        self.sleep = sleep
        self.clock = clock
        self._lock = threading.Lock()
        self._last_call: float | None = None
        self._semaphore = threading.BoundedSemaphore(max_concurrency)

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                json_body: JsonRecord | None = None, timeout: float = 15.0) -> HttpResponse:
        with self._semaphore:
            with self._lock:
                now = self.clock()
                if self._last_call is not None:
                    remaining = self.min_interval_seconds - (now - self._last_call)
                    if remaining > 0:
                        self.sleep(remaining)
                self._last_call = self.clock()
            return self.upstream.request(method, url, headers=headers,
                                         json_body=json_body, timeout=timeout)


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

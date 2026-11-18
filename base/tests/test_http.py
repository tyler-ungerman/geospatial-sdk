import json
from urllib.parse import urlencode

import pytest

from base.datasources import RetryingTransport, CachedTransport, HttpResponse, SourceUnavailable
from base.datasources.http import CircuitOpen
from base.datasources.http import http_get


class Fake:
    def __init__(self, statuses):
        self.statuses = statuses
        self.calls = 0

    def request(self, method, url, *, headers=None, json_body=None, timeout=15):
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return HttpResponse(status, b'{"ok":1}')


def test_http_retry_on_transient_get_no_retry_on_post():
    calls = []
    fake = Fake([429, 503, 200])
    client = RetryingTransport(fake, attempts=3, backoff_seconds=.1, sleep=calls.append,
                               jitter=lambda low, high: high)
    assert http_get(client, "https://example.test/data").json() == {"ok": 1}
    assert fake.calls == 3
    assert calls == [.1, .2]
    fake = Fake([429, 200])
    client = RetryingTransport(fake, attempts=3, sleep=lambda _: None)
    assert client.request("POST", "https://example.test", json_body={"a": 1}).status == 429
    assert fake.calls == 1


def test_retry_after_seconds_and_http_date_are_honored():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    class RetryAfterFake:
        def __init__(self, value): self.value, self.calls = value, 0
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            if self.calls == 1:
                return HttpResponse(429, b"slow down", {"Retry-After": self.value})
            return HttpResponse(200, b'{"ok":true}')

    sleeps = []
    fake = RetryAfterFake("2")
    client = RetryingTransport(fake, attempts=2, sleep=sleeps.append)
    assert http_get(client, "https://example.test").status == 200
    assert sleeps == [2.0]

    sleeps.clear()
    retry_date = format_datetime(datetime.now(timezone.utc)+timedelta(seconds=5), usegmt=True)
    fake = RetryAfterFake(retry_date)
    client = RetryingTransport(fake, attempts=2, sleep=sleeps.append)
    assert http_get(client, "https://example.test").status == 200
    assert 0 <= sleeps[0] <= 5


def test_open_circuit_is_not_retried():
    class Open:
        calls = 0
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            raise CircuitOpen("cooling down")
    upstream = Open()
    sleeps = []
    client = RetryingTransport(upstream, attempts=3, sleep=sleeps.append)
    with pytest.raises(CircuitOpen):
        client.request("GET", "https://example.test")
    assert upstream.calls == 1
    assert sleeps == []


def test_retry_exhaustion_raises_clear_source_error():
    fake = Fake([503])
    client = RetryingTransport(fake, attempts=2, sleep=lambda _: None)
    with pytest.raises(SourceUnavailable, match="HTTP 503"):
        http_get(client, "https://example.test/unavailable")
    assert fake.calls == 2


def test_http_cache_and_auth_exclusion(tmp_path):
    fake = Fake([200])
    cached = CachedTransport(fake, tmp_path)
    assert cached.request("GET", "https://example.test/a").status == 200
    assert cached.request("GET", "https://example.test/a").status == 200
    assert fake.calls == 1
    assert cached.request("GET", "https://example.test/a", headers={"Authorization": "Bearer confidential"}).status == 200
    assert fake.calls == 2
    assert cached.request("GET", "https://example.test/a?api_key=private").status == 200
    assert fake.calls == 3
    assert len(list(tmp_path.iterdir())) == 1

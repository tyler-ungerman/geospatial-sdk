import json
from urllib.parse import urlencode

import pytest

from base.datasources import RetryingTransport, CachedTransport, HttpResponse, SourceUnavailable
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
    client = RetryingTransport(fake, attempts=3, backoff_seconds=.1, sleep=calls.append)
    assert http_get(client, "https://example.test/data").json() == {"ok": 1}
    assert fake.calls == 3
    assert calls == [.1, .2]
    fake = Fake([429, 200])
    client = RetryingTransport(fake, attempts=3, sleep=lambda _: None)
    assert client.request("POST", "https://example.test", json_body={"a": 1}).status == 429
    assert fake.calls == 1


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

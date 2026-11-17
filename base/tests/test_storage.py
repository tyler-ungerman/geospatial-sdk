import copy
import io
import json
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from base.storage import LocalJSONStore, R2Store, RunScopedStore, run_pipeline, normalize_event, event_id
from base.storage.backends import validate_key
from base.datasources import fetch_all
from fixtures import FakeTransport

NOW = datetime(2026, 11, 17, 11, 0, tzinfo=timezone.utc)


def test_store_import_does_not_need_credentials():
    # Missing credentials must only fail upon invoking a remote operation.
    with patch.dict(os.environ, {}, clear=True):
        from base.storage import db
        assert db is not None
        with pytest.raises(ValueError):
            R2Store().read_json("not-a-real-key")


def test_local_store_and_path_safety(tmp_path):
    store = LocalJSONStore(tmp_path)
    assert store.read_json("unwritten.json", default=[]) == []
    assert store.write_json("test/data.json", {"a": 2})
    assert store.exists("test/data.json")
    assert store.read_json("test/data.json") == {"a": 2}
    assert store.list_keys("test/") == ["test/data.json"]
    assert store.delete("test/data.json")
    assert not store.exists("test/data.json")
    for bad in ("../secret", "/etc/passwd", "../secret", "..\\name", "x/../y", "a//b", ""):
        with pytest.raises(ValueError):
            store.write_json(bad, {})


def test_pipeline_processes_all_six_and_keeps_fires_distinct(tmp_path):
    store = LocalJSONStore(tmp_path)
    summary = run_pipeline(client=FakeTransport(), store=store, now=NOW,
                           options={"google_news": {"inter_feed_delay": 0}})
    assert summary["errors"] == {}
    assert summary["events_fetched"] == 7, summary
    assert summary["entities"] == {"opensky": 2}
    events = store.read_json("geospatial/events/2026-11-17.json")
    assert len(events) == 7
    fires = [e for e in events if e["source"] == "NASA FIRMS"]
    assert len(fires) == 2 and len({e["id"] for e in fires}) == 2
    assert all("location_uncertainty_m" in e for e in events)
    assert next(e for e in events if str(e["source"]).startswith("GDELT"))["location_basis"] is None
    summary2 = run_pipeline(client=FakeTransport(), store=store, now=NOW,
                            options={"google_news": {"inter_feed_delay": 0}})
    assert summary2["events_new"] == 0
    assert len(store.read_json("geospatial/events/2026-11-17.json")) == 7


def test_failed_source_preserves_existing_store(tmp_path):
    store = LocalJSONStore(tmp_path)
    run_pipeline(["opensky"], client=FakeTransport(), store=store, now=NOW)
    old = store.read_json("geospatial/entities/opensky.json")
    summary = run_pipeline(["opensky", "usgs"], client=FakeTransport(failure="opensky-network.org"),
                           store=store, now=NOW)
    assert "opensky" in summary["errors"]
    assert store.read_json("geospatial/entities/opensky.json") == old
    assert summary["events_fetched"] == 2


class PartialWriteStore(LocalJSONStore):
    def write_json(self, key, data):
        super().write_json(key, data)
        if key.endswith("fail.json"):
            raise OSError("timeout after partial write")
        return True


def test_run_scope_cleanup_even_on_partial_success(tmp_path):
    base = PartialWriteStore(tmp_path)
    base.write_json("production/data.json", {"sentinel": 1})
    holder = None
    with pytest.raises(OSError):
        with RunScopedStore(base) as store:
            holder = store
            store.write_json("okay.json", {"x": 3})
            store.write_json("fail.json", {"x": 3})
    assert holder is not None
    assert base.list_keys() == ["production/data.json"]
    with pytest.raises(ValueError):
        holder.key("../data.json")
    with pytest.raises(ValueError):
        RunScopedStore(base, run_id="nonsense")


class FakeR2Client:
    def __init__(self):
        self.data = {}
    def put_object(self, **kw):
        self.data[kw["Key"]] = kw["Body"]
    def get_object(self, **kw):
        if kw["Key"] not in self.data:
            err = RuntimeError("404")
            err.response = {"Error": {"Code": "NoSuchKey"}}
            raise err
        return {"Body": io.BytesIO(self.data[kw["Key"]])}
    def head_object(self, **kw):
        return self.get_object(**kw)
    def delete_object(self, **kw):
        self.data.pop(kw["Key"], None)
    def get_paginator(self, method):
        parent = self
        class Paginator:
            def paginate(self, *, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for k in parent.data if k.startswith(Prefix)]}]
        return Paginator()


def test_fake_remote_roundtrip_no_network():
    fake = FakeR2Client()
    store = R2Store(client=fake, bucket_name="test")
    assert store.read_json("missing.json", default={"not-found": True}) == {"not-found": True}
    store.write_json("foo.json", [1, 2])
    assert store.exists("foo.json")
    assert store.read_json("foo.json") == [1, 2]
    assert store.list_keys() == ["foo.json"]
    with RunScopedStore(store) as scoped:
        scoped.write_json("tmp.json", {"x": 1})
        assert scoped.read_json("tmp.json")["x"] == 1
    assert store.list_keys() == ["foo.json"]
    store.delete("foo.json")
    assert not store.exists("foo.json")


def test_non_destructive_smoke_runs_full_pipeline_and_cleans(tmp_path):
    from base.storage import smoke_sources
    store = LocalJSONStore(tmp_path)
    store.write_json("geospatial/events/production.json", {"production": True})
    result = smoke_sources(store, ["usgs", "firms", "opensky"],
                           client=FakeTransport(), now=NOW)
    assert result["summary"]["events_fetched"] == 4
    assert any(k.endswith("entities/opensky.json") for k in result["inspected"])
    assert store.list_keys() == ["geospatial/events/production.json"]


def test_store_rejects_symlink_escape(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    root = tmp_path / "safe"
    root.mkdir()
    (root / "linked").symlink_to(outer)
    with pytest.raises(ValueError):
        LocalJSONStore(root).write_json("linked/file.json", {"private": True})

"""Local / R2 JSON object stores with safe keys and isolated temporary runs."""

from __future__ import annotations

import json
import copy
import os
import re
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Protocol


def validate_key(key: str) -> str:
    """Reject traversal, absolute paths, backslashes and ambiguous object keys."""
    if not isinstance(key, str) or not key or '\\' in key or '\x00' in key:
        raise ValueError("Invalid object key")
    path = PurePosixPath(key)
    if path.is_absolute() or any(part in (".", "..") for part in key.split("/")) or "//" in key:
        raise ValueError("Object key must be a relative POSIX path without traversal")
    return key


class JSONStore(Protocol):
    def write_json(self, key: str, data: dict | list) -> bool: ...
    def read_json(self, key: str, default: Any = None) -> Any: ...
    def delete(self, key: str) -> bool: ...
    def exists(self, key: str) -> bool: ...
    def list_keys(self, prefix: str = "") -> list[str]: ...


class LocalJSONStore:
    """Atomic, directory-sandboxed local JSON object store, for dev and offline tests."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve(strict=False)
        if not path.is_relative_to(self.root):
            raise ValueError("Object path escapes configured root via symlink")
        return path

    def write_json(self, key: str, data: dict | list) -> bool:
        if not isinstance(data, (dict, list)):
            raise TypeError("write_json expects dict or list")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, path)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)
        return True

    def read_json(self, key: str, default: Any = None) -> Any:
        try:
            return json.loads(self._path(key).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str = "") -> list[str]:
        if prefix:
            validate_key(prefix)
        if not self.root.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                      if p.is_file() and p.relative_to(self.root).as_posix().startswith(prefix))

    def delete(self, key: str) -> bool:
        self._path(key).unlink(missing_ok=True)
        return True


class R2Store:
    """R2 via S3 API. No connection, boto3 import, or credential lookup until first operation.

    Config from explicit arguments or environment (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME). To load .env use your app's own bootstrap.
    """

    def __init__(self, *, account_id: str | None = None, access_key_id: str | None = None,
                 secret_access_key: str | None = None, bucket_name: str | None = None,
                 client: Any = None):
        self.account_id = account_id
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket_name = bucket_name
        self._client = client

    def _connect(self) -> tuple[Any, str]:
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise RuntimeError("Install geospatial-base-sdk[r2] to use R2Store") from exc
            account = self.account_id or os.getenv("R2_ACCOUNT_ID")
            access = self.access_key_id or os.getenv("R2_ACCESS_KEY_ID")
            secret = self.secret_access_key or os.getenv("R2_SECRET_ACCESS_KEY")
            bucket = self.bucket_name or os.getenv("R2_BUCKET_NAME")
            if not all([account, access, secret, bucket]):
                raise ValueError("Missing R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY or R2_BUCKET_NAME")
            self._client = boto3.client("s3", endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
                                       aws_access_key_id=access, aws_secret_access_key=secret,
                                       region_name="auto", config=Config(s3={"addressing_style": "path"}))
        bucket = self.bucket_name or os.getenv("R2_BUCKET_NAME")
        if not bucket:
            raise ValueError("R2 bucket is required")
        return self._client, bucket

    @staticmethod
    def _not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        return str(error.get("Code")) in ("NoSuchKey", "404", "NotFound")

    def write_json(self, key: str, data: dict | list) -> bool:
        client, bucket = self._connect()
        client.put_object(Bucket=bucket, Key=validate_key(key),
                          Body=json.dumps(data, allow_nan=False).encode("utf-8"),
                          ContentType="application/json")
        return True

    def read_json(self, key: str, default: Any = None) -> Any:
        client, bucket = self._connect()
        try:
            return json.loads(client.get_object(Bucket=bucket, Key=validate_key(key))["Body"].read().decode("utf-8"))
        except Exception as exc:
            if self._not_found(exc):
                return default
            raise

    def exists(self, key: str) -> bool:
        client, bucket = self._connect()
        try:
            client.head_object(Bucket=bucket, Key=validate_key(key))
            return True
        except Exception as exc:
            if self._not_found(exc):
                return False
            raise

    def list_keys(self, prefix: str = "") -> list[str]:
        client, bucket = self._connect()
        if prefix:
            validate_key(prefix)
        return [obj["Key"] for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
                for obj in page.get("Contents", [])]

    def delete(self, key: str) -> bool:
        client, bucket = self._connect()
        client.delete_object(Bucket=bucket, Key=validate_key(key))
        return True

    def write_text(self, key: str, value: str) -> bool:
        client, bucket = self._connect()
        client.put_object(Bucket=bucket, Key=validate_key(key), Body=value.encode("utf-8"),
                          ContentType="text/plain")
        return True

    def read_text(self, key: str, default: Any = None) -> str | Any:
        client, bucket = self._connect()
        try:
            return client.get_object(Bucket=bucket, Key=validate_key(key))["Body"].read().decode("utf-8")
        except Exception as exc:
            if self._not_found(exc):
                return default
            raise

    def upload_file(self, local_path: str, key: str) -> bool:
        client, bucket = self._connect()
        client.upload_file(local_path, bucket, validate_key(key))
        return True

    def download_file(self, key: str, local_path: str) -> bool:
        client, bucket = self._connect()
        client.download_file(bucket, validate_key(key), local_path)
        return True


class RunScopedStore:
    """Test-only namespace with best-effort cleanup even when a write partially succeeds.

    Never scans or deletes anything outside its randomly generated run prefix.
    """

    def __init__(self, backend: JSONStore, *, namespace: str = "smoke-tests", run_id: str | None = None):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", namespace):
            raise ValueError("namespace must be lowercase alphanumeric/hyphens")
        self.run_id = run_id or uuid.uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}", self.run_id):
            raise ValueError("run_id must be a 32-character lowercase UUID-hex string")
        self.backend = backend
        self.prefix = f"{namespace}/{self.run_id}/"
        self._touched: set[str] = set()
        self._expected: dict[str, dict | list] = {}
        # Be defensive if a run ID is ever reused by accident.
        if self.backend.list_keys(self.prefix):
            raise ValueError("Run-scoped prefix already exists; refusing to overwrite")

    def key(self, relative: str) -> str:
        return self.prefix + validate_key(relative)

    def write_json(self, key: str, data: dict | list) -> bool:
        full = self.key(key)
        self._touched.add(full)  # before write: handles timeout after remote put succeeded
        self._expected[full] = copy.deepcopy(data)
        return self.backend.write_json(full, data)

    def read_json(self, key: str, default: Any = None) -> Any:
        return self.backend.read_json(self.key(key), default)

    def verify_readback(self) -> None:
        """Compare every object written during the run against its persisted JSON."""
        for key, expected in self._expected.items():
            actual = self.backend.read_json(key, default=None)
            if actual != expected:
                raise RuntimeError(f"Smoke read-back mismatch for {key}")

    def exists(self, key: str) -> bool:
        return self.backend.exists(self.key(key))

    def list_keys(self, prefix: str = "") -> list[str]:
        full_prefix = self.prefix + (validate_key(prefix) if prefix else "")
        return [key[len(self.prefix):] for key in self.backend.list_keys(full_prefix)]

    def delete(self, key: str) -> bool:
        full = self.key(key)
        self._touched.add(full)
        return self.backend.delete(full)

    def cleanup(self) -> None:
        # Include all keys inside this UUID namespace to cover partial writes.
        keys = set(self.backend.list_keys(self.prefix)) | self._touched
        for key in sorted(keys, reverse=True):
            self.backend.delete(key)

    def __enter__(self) -> "RunScopedStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.cleanup()

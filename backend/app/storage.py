"""File storage: local disk (dev) or Supabase Storage (prod).

Key layout:
- `u/<owner_id>/...`  one user's files (uploads, renders of uploads, template files, exports). App code only
                      ever sees a user's storage through `OwnerStorage`, which prefixes every key and refuses
                      keys of other owners.
- `baseline/...`      the shared demo files (scenario PDFs, their renders), stored once for every user and
                      read-only: `put` refuses these keys; only app.baseline publishes them, via
                      `put_baseline`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

import httpx

from app.settings import Settings

BASELINE_PREFIX = "baseline/"
USER_PREFIX = "u/"


class ReadOnlyKeyError(PermissionError):
    """A write to baseline/ (or to another owner's prefix)."""


class Storage(Protocol):
    name: str

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def health(self) -> bool: ...


class BaseStorage(Storage, Protocol):
    def put_baseline(self, key: str, data: bytes, content_type: str | None = None) -> str: ...


def _check_writable(key: str) -> None:
    if key.startswith(BASELINE_PREFIX):
        raise ReadOnlyKeyError(f"baseline files are read-only: {key}")


class LocalStorage:
    name = "local"

    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError(f"storage key escapes the root: {key}")
        return p

    def _write(self, key: str, data: bytes) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        _check_writable(key)
        return self._write(key, data)

    def put_baseline(self, key: str, data: bytes, content_type: str | None = None) -> str:
        if not key.startswith(BASELINE_PREFIX):
            raise ValueError(f"not a baseline key: {key}")
        return self._write(key, data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete_prefix(self, prefix: str) -> int:
        _check_writable(prefix)
        p = self._path(prefix)
        if not p.exists() or p == self.root.resolve():
            return 0
        n = sum(1 for f in p.rglob("*") if f.is_file()) if p.is_dir() else 1
        shutil.rmtree(p) if p.is_dir() else p.unlink()
        return n

    def health(self) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root.is_dir()


class SupabaseStorage:
    name = "supabase"

    def __init__(self, url: str, service_key: str, bucket: str):
        self.base = f"{url.rstrip('/')}/storage/v1"
        self.bucket = bucket
        self.headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    def _write(self, key: str, data: bytes, content_type: str | None) -> str:
        headers = {
            **self.headers,
            "x-upsert": "true",
            "Content-Type": content_type or "application/octet-stream",
        }
        r = httpx.post(f"{self.base}/object/{self.bucket}/{key}", content=data, headers=headers, timeout=60)
        r.raise_for_status()
        return key

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        _check_writable(key)
        return self._write(key, data, content_type)

    def put_baseline(self, key: str, data: bytes, content_type: str | None = None) -> str:
        if not key.startswith(BASELINE_PREFIX):
            raise ValueError(f"not a baseline key: {key}")
        return self._write(key, data, content_type)

    def get(self, key: str) -> bytes:
        r = httpx.get(f"{self.base}/object/{self.bucket}/{key}", headers=self.headers, timeout=60)
        if r.status_code in (400, 404):  # Supabase answers a missing object with 400 or 404
            raise FileNotFoundError(key)
        r.raise_for_status()
        return r.content

    def _list(self, prefix: str) -> list[str]:
        """Every object key under `prefix` (folders are listed level by level)."""
        keys: list[str] = []
        folders = [prefix.rstrip("/")]
        while folders:
            folder = folders.pop()
            offset = 0
            while True:
                r = httpx.post(f"{self.base}/object/list/{self.bucket}", headers=self.headers, timeout=30,
                               json={"prefix": folder, "limit": 1000, "offset": offset})
                r.raise_for_status()
                entries = r.json()
                for e in entries:
                    path = f"{folder}/{e['name']}"
                    (keys if e.get("id") else folders).append(path)  # folders have no id
                if len(entries) < 1000:
                    break
                offset += 1000
        return keys

    def delete_prefix(self, prefix: str) -> int:
        _check_writable(prefix)
        keys = self._list(prefix)
        for i in range(0, len(keys), 1000):
            r = httpx.request("DELETE", f"{self.base}/object/{self.bucket}", headers=self.headers, timeout=60,
                              json={"prefixes": keys[i : i + 1000]})
            r.raise_for_status()
        return len(keys)

    def health(self) -> bool:
        try:
            r = httpx.get(f"{self.base}/bucket/{self.bucket}", headers=self.headers, timeout=10)
            return r.status_code == 200
        except httpx.HTTPError:
            return False


class OwnerStorage:
    """One user's view of the storage: keys are relative to `u/<owner_id>/` (a full key under that prefix,
    as stored in documents.storage_key, is accepted too). baseline/ keys are readable, never writable; other
    owners' keys are refused."""

    def __init__(self, inner: Storage, owner_id: str):
        self.inner = inner
        self.owner_id = owner_id
        self.prefix = f"{USER_PREFIX}{owner_id}/"
        self.name = inner.name

    def key(self, key: str) -> str:
        parts = key.split("/")
        if any(p in ("", ".", "..") for p in parts[:-1]) or parts[-1] in (".", ".."):
            raise ValueError(f"invalid storage key: {key}")
        if key.startswith(self.prefix):
            return key
        if key.startswith((USER_PREFIX, BASELINE_PREFIX)):
            raise ReadOnlyKeyError(f"not this workspace's key: {key}")
        return self.prefix + key

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        return self.inner.put(self.key(key), data, content_type)

    def get(self, key: str) -> bytes:
        if key.startswith(BASELINE_PREFIX):
            return self.inner.get(key)
        return self.inner.get(self.key(key))

    def delete_prefix(self, prefix: str) -> int:
        return self.inner.delete_prefix(self.key(prefix) if prefix else self.prefix)

    def health(self) -> bool:
        return self.inner.health()


def make_storage(settings: Settings) -> BaseStorage:
    if settings.storage_backend == "supabase":
        return SupabaseStorage(settings.supabase_url, settings.supabase_service_key, settings.supabase_bucket)
    return LocalStorage(settings.storage_dir)

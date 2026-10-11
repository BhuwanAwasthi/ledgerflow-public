"""File storage: local disk (dev) or Supabase Storage (prod)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx

from app.settings import Settings


class Storage(Protocol):
    name: str

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str: ...
    def get(self, key: str) -> bytes: ...
    def health(self) -> bool: ...


class LocalStorage:
    name = "local"

    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError(f"storage key escapes the root: {key}")
        return p

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def health(self) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root.is_dir()


class SupabaseStorage:
    name = "supabase"

    def __init__(self, url: str, service_key: str, bucket: str):
        self.base = f"{url.rstrip('/')}/storage/v1"
        self.bucket = bucket
        self.headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        headers = {
            **self.headers,
            "x-upsert": "true",
            "Content-Type": content_type or "application/octet-stream",
        }
        r = httpx.post(f"{self.base}/object/{self.bucket}/{key}", content=data, headers=headers, timeout=60)
        r.raise_for_status()
        return key

    def get(self, key: str) -> bytes:
        r = httpx.get(f"{self.base}/object/{self.bucket}/{key}", headers=self.headers, timeout=60)
        r.raise_for_status()
        return r.content

    def health(self) -> bool:
        try:
            r = httpx.get(f"{self.base}/bucket/{self.bucket}", headers=self.headers, timeout=10)
            return r.status_code == 200
        except httpx.HTTPError:
            return False


def make_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "supabase":
        return SupabaseStorage(settings.supabase_url, settings.supabase_service_key, settings.supabase_bucket)
    return LocalStorage(settings.storage_dir)

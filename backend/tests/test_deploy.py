"""B5 deploy contract: Supabase storage adapter, Postgres URL handling, render.yaml and vercel.json."""

from __future__ import annotations

import json

import httpx
import pytest
import yaml

from app import storage as storage_mod
from app.db import normalise_url
from app.settings import REPO_ROOT, Settings
from app.storage import ReadOnlyKeyError, SupabaseStorage, make_storage


class FakeSupabase:
    """Just enough of the Supabase Storage REST API: upsert, download and bucket lookup."""

    def __init__(self, bucket: str = "documents"):
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def post(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> httpx.Response:
        self.calls.append(("POST", url, headers))
        prefix = f"https://proj.supabase.co/storage/v1/object/{self.bucket}/"
        assert url.startswith(prefix)
        self.objects[url[len(prefix):]] = content
        return httpx.Response(200, json={"Key": url}, request=httpx.Request("POST", url))

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
        self.calls.append(("GET", url, headers))
        req = httpx.Request("GET", url)
        if url.endswith(f"/bucket/{self.bucket}"):
            return httpx.Response(200, json={"id": self.bucket}, request=req)
        key = url.split(f"/object/{self.bucket}/", 1)[1]
        if key not in self.objects:
            return httpx.Response(400, json={"error": "not_found"}, request=req)
        return httpx.Response(200, content=self.objects[key], request=req)


@pytest.fixture
def fake_supabase(monkeypatch: pytest.MonkeyPatch) -> FakeSupabase:
    fake = FakeSupabase()
    monkeypatch.setattr(storage_mod.httpx, "post", fake.post)
    monkeypatch.setattr(storage_mod.httpx, "get", fake.get)
    return fake


def test_make_storage_picks_supabase_from_settings() -> None:
    s = Settings(storage_backend="supabase", supabase_url="https://proj.supabase.co/",
                 supabase_service_key="k", supabase_bucket="documents")
    st = make_storage(s)
    assert isinstance(st, SupabaseStorage)
    assert st.base == "https://proj.supabase.co/storage/v1"


def test_supabase_storage_roundtrip_and_health(fake_supabase: FakeSupabase) -> None:
    st = SupabaseStorage("https://proj.supabase.co", "service-key", "documents")
    key = st.put("cases/1/abc_invoice.pdf", b"%PDF-1.7", "application/pdf")
    assert key == "cases/1/abc_invoice.pdf"
    assert st.get(key) == b"%PDF-1.7"
    assert st.health() is True
    method, _, headers = fake_supabase.calls[0]
    assert method == "POST"
    assert headers["Authorization"] == "Bearer service-key" and headers["apikey"] == "service-key"
    assert headers["x-upsert"] == "true" and headers["Content-Type"] == "application/pdf"


def test_supabase_storage_missing_object_raises(fake_supabase: FakeSupabase) -> None:
    st = SupabaseStorage("https://proj.supabase.co", "k", "documents")
    with pytest.raises(FileNotFoundError):  # the same contract as LocalStorage (app.baseline relies on it)
        st.get("cases/9/missing.pdf")


def test_supabase_storage_baseline_is_read_only(fake_supabase: FakeSupabase) -> None:
    st = SupabaseStorage("https://proj.supabase.co", "k", "documents")
    with pytest.raises(ReadOnlyKeyError):
        st.put("baseline/fixtures/x.pdf", b"%PDF")
    assert st.put_baseline("baseline/fixtures/x.pdf", b"%PDF") == "baseline/fixtures/x.pdf"
    assert st.get("baseline/fixtures/x.pdf") == b"%PDF"


def test_supabase_health_false_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, **_: object) -> httpx.Response:
        raise httpx.ConnectError("down", request=httpx.Request("GET", url))

    monkeypatch.setattr(storage_mod.httpx, "get", boom)
    assert SupabaseStorage("https://proj.supabase.co", "k", "documents").health() is False


@pytest.mark.parametrize("raw", [
    "postgresql://postgres.abcd:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres",
    "postgres://postgres.abcd:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres",
])
def test_supabase_pooler_url_uses_psycopg3(raw: str) -> None:
    assert normalise_url(raw) == (
        "postgresql+psycopg://postgres.abcd:pw@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    )


def test_render_blueprint() -> None:
    bp = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    (svc,) = bp["services"]
    assert svc["type"] == "web" and svc["plan"] == "free" and svc["runtime"] == "python"
    assert "rootDir" not in svc, "the API reads ../config and ../data, so the whole repo must be deployed"
    assert "uv" in svc["buildCommand"] and "sync" in svc["buildCommand"]
    start = svc["startCommand"]
    assert "uvicorn app.main:app --host 0.0.0.0 --port $PORT" in start
    assert 0 <= start.find("alembic upgrade head") < start.find("uvicorn"), "migrations run before the API starts"
    assert svc["healthCheckPath"] == "/healthz"
    keys = {e["key"]: e for e in svc["envVars"]}
    for secret in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT", "DATABASE_URL",
                   "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "CORS_ORIGINS", "PUBLIC_APP_URL"):
        assert keys[secret].get("sync") is False, f"{secret} must be set in the dashboard, not committed"
    assert keys["STORAGE_BACKEND"]["value"] == "supabase"
    assert keys["AUTH_MODE"]["value"] == "supabase"  # never the dev stub on Render (it refuses to start there)
    assert keys["FILE_TOKEN_SECRET"].get("generateValue") is True
    py = keys["PYTHON_VERSION"]["value"]
    assert py.startswith("3.12."), "pyproject pins python >=3.12,<3.13"


def test_vercel_spa_rewrite() -> None:
    cfg = json.loads((REPO_ROOT / "frontend" / "vercel.json").read_text(encoding="utf-8"))
    assert {"source": "/(.*)", "destination": "/index.html"} in cfg["rewrites"]
    assert cfg["outputDirectory"] == "dist"


def test_cors_always_keeps_localhost_dev_origin() -> None:
    s = Settings(cors_origins="https://ledgerflow.vercel.app, https://ledgerflow-git-main.vercel.app")
    assert s.cors_origin_list == [
        "https://ledgerflow.vercel.app", "https://ledgerflow-git-main.vercel.app", "http://localhost:5173",
    ]


def test_long_upload_names_fit_postgres_columns() -> None:
    from app.cases import UploadedFile

    f = UploadedFile("x" * 400 + ".pdf", "application/" + "y" * 200, b"")
    assert len(f.filename) == 255 and f.filename.endswith(".pdf")
    assert f.mime is None
    assert UploadedFile("invoice.pdf", "application/pdf", b"").filename == "invoice.pdf"

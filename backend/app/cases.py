"""Case creation (shared by the upload API, fixtures and tests)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config_loader import ResolvedConfig, snapshot_json
from app.models import Case, CaseStatus, Document
from app.storage import Storage


@dataclass
class UploadedFile:
    filename: str
    mime: str | None
    data: bytes


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "file"


def create_case(
    s: Session,
    cfg: ResolvedConfig,
    storage: Storage,
    files: list[UploadedFile],
    *,
    submitter_name: str | None = None,
    submitter_email: str | None = None,
    note: str | None = None,
    source: str = "upload",
    parent_case_id: int | None = None,
) -> Case:
    case = Case(
        submitter_name=submitter_name,
        submitter_email=submitter_email,
        note=note,
        source=source,
        status=CaseStatus.received,
        parent_case_id=parent_case_id,
        config_hash=cfg.hash,
        config_snapshot=snapshot_json(cfg),
    )
    s.add(case)
    s.flush()
    case.ref = f"{cfg.app.app.case_ref_prefix}{case.id:04d}"
    for f in files:
        sha = hashlib.sha256(f.data).hexdigest()
        key = storage.put(f"cases/{case.id}/{sha[:12]}_{_safe_name(f.filename)}", f.data, f.mime)
        s.add(Document(case_id=case.id, filename=f.filename, mime=f.mime, sha256=sha, storage_key=key))
    s.commit()
    return case

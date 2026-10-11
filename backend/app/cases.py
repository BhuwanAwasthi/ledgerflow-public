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

    def __post_init__(self) -> None:
        # Postgres enforces documents.filename String(255) / mime String(128); SQLite would not catch it.
        if len(self.filename) > 255:
            stem, dot, ext = self.filename.rpartition(".")
            ext = f".{ext}" if dot and len(ext) <= 10 else ""
            self.filename = (stem if ext else self.filename)[: 255 - len(ext)] + ext
        if self.mime and len(self.mime) > 128:
            self.mime = None


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


def create_child_case(
    s: Session, cfg: ResolvedConfig, parent: Case, documents: list[Document], note: str
) -> Case:
    """A case for one invoice of a bundle: same submitter, source and config snapshot as the parent; its
    documents point at the parent's stored file with a page range (no bytes copied)."""
    child = Case(
        submitter_name=parent.submitter_name,
        submitter_email=parent.submitter_email,
        note=note,
        source=parent.source,
        status=CaseStatus.received,
        parent_case_id=parent.id,
        config_hash=parent.config_hash or cfg.hash,
        config_snapshot=parent.config_snapshot or snapshot_json(cfg),
    )
    s.add(child)
    s.flush()
    child.ref = f"{cfg.app.app.case_ref_prefix}{child.id:04d}"
    for d in documents:
        d.case_id = child.id
        s.add(d)
    s.flush()
    return child

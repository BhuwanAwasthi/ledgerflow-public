"""Exports of approved invoices: a single template file, a ZIP of files, or one batch import file built from
the ERP profile's `batch` section (all selected invoices' rows, header once). Every export is an
export_batch row; exporting a case twice needs `force` (the re-export guard returns 409 otherwise).
"""

from __future__ import annotations

import io
import zipfile
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.case_state import fold_state
from app.config_loader import ErpProfile, ResolvedConfig, from_snapshot
from app.exports.context import template_context
from app.exports.render import Row, make_env, render_rows, render_string, to_bytes
from app.models import Case, CaseStatus, ExportBatch, TemplateVersion, utcnow
from app.storage import Storage

EXPORTABLE = (CaseStatus.approved, CaseStatus.exported)


class ExportError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def get_profile(cfg: ResolvedConfig, profile_id: str | None) -> ErpProfile:
    pid = profile_id or cfg.app.app.default_erp_profile
    if pid not in cfg.erp_profiles:
        raise ExportError(422, f"unknown ERP profile '{pid}' (have: {', '.join(cfg.erp_profiles)})")
    return cfg.erp_profiles[pid]


def final_template(s: Session, case_id: int) -> TemplateVersion | None:
    return s.scalars(
        select(TemplateVersion)
        .where(TemplateVersion.case_id == case_id, TemplateVersion.status == "final")
        .order_by(TemplateVersion.version.desc())
    ).first()


def case_rows(s: Session, case: Case, profile: ErpProfile) -> tuple[list[Row], int]:
    """The approved rows in `profile`: the frozen version's rows when it used that profile, else the same
    case data rendered with the other profile. Returns (rows, version)."""
    tv = final_template(s, case.id)
    if tv is None:
        raise ExportError(409, f"{case.ref} has no final template (approve it first)")
    rows = list(tv.rows) if tv.erp_profile == profile.id else render_rows(
        profile, template_context(s, case, fold_state(s, case.id), tv.version)
    ).rows
    empty = sorted({c.header for c in profile.columns if c.required for r in rows if not r.get(c.header)})
    if empty:
        raise ExportError(422, f"{case.ref}: required column(s) empty in {profile.name}: {', '.join(empty)}")
    return rows, tv.version


def case_file(s: Session, case: Case, profile: ErpProfile, version: int | None = None) -> tuple[bytes, str, str]:
    """(bytes, media type, filename) of one case's template; `version` picks a stored version (any status)."""
    if version is None:
        rows, version = case_rows(s, case, profile)
    else:
        tv = s.scalars(
            select(TemplateVersion).where(TemplateVersion.case_id == case.id, TemplateVersion.version == version)
        ).first()
        if tv is None:
            raise ExportError(404, f"{case.ref} has no template v{version}")
        if tv.erp_profile != profile.id:
            profile = profile_of(s, tv, profile)
        rows = list(tv.rows)
    data, mime = to_bytes(profile, rows)
    name = render_string(make_env(profile), profile.filename_template, {"case": {"ref": case.ref, "id": case.id}, "version": version})
    return data, mime, name


def profile_of(s: Session, tv: TemplateVersion, fallback: ErpProfile) -> ErpProfile:
    case = s.get(Case, tv.case_id)
    snapshot = (case.config_snapshot or {}) if case else {}
    try:  # the profile as configured when the case ran
        return from_snapshot(snapshot).erp_profiles[tv.erp_profile]
    except (KeyError, ValueError, TypeError):
        return fallback


def next_ref(s: Session, cfg: ResolvedConfig) -> str:
    n = (s.scalar(select(func.count()).select_from(ExportBatch)) or 0) + 1
    return f"{cfg.app.exports.batch_ref_prefix}{n:04d}"


def create_export(
    s: Session,
    storage: Storage,
    cfg: ResolvedConfig,
    case_ids: list[int],
    profile_id: str | None,
    mode: Literal["zip", "batch"],
    actor: str,
    force: bool = False,
) -> ExportBatch:
    profile = get_profile(cfg, profile_id)
    ids = list(dict.fromkeys(case_ids))
    if not ids:
        raise ExportError(422, "select at least one approved invoice")
    cases = {c.id: c for c in s.scalars(select(Case).where(Case.id.in_(ids))).all()}
    missing = [i for i in ids if i not in cases]
    if missing:
        raise ExportError(404, f"case(s) not found: {missing}")
    ordered = [cases[i] for i in ids]
    not_ready = [c.ref for c in ordered if c.status not in EXPORTABLE]
    if not_ready:
        raise ExportError(409, f"only approved invoices can be exported: {', '.join(r or '' for r in not_ready)}")
    done = [c.ref for c in ordered if c.status == CaseStatus.exported or c.exported_at]
    if done and not force:
        raise ExportError(409, f"already exported: {', '.join(r or '' for r in done)}. Pass force=true to export again.")

    ref = next_ref(s, cfg)
    env = make_env(profile)
    batch_vars: dict[str, Any] = {"batch": {"ref": ref}, "profile": {"id": profile.id, "name": profile.name}}
    if mode == "batch":
        if profile.batch.mode != "concat_rows":
            raise ExportError(422, f"batch mode '{profile.batch.mode}' of profile {profile.id} is not supported")
        rows: list[Row] = []
        for c in ordered:
            rows += case_rows(s, c, profile)[0]
        data, mime = to_bytes(profile, rows)
        filename = render_string(env, profile.batch.filename_template, batch_vars)
    elif mode == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for c in ordered:
                fdata, _, fname = case_file(s, c, profile)
                zf.writestr(fname, fdata)
        data, mime = buf.getvalue(), "application/zip"
        filename = render_string(env, cfg.app.exports.zip_filename_template, batch_vars)
    else:
        raise ExportError(422, f"unknown export mode '{mode}' (zip | batch)")

    key = storage.put(f"exports/batches/{ref}/{filename}", data, mime)
    batch = ExportBatch(ref=ref, erp_profile=profile.id, case_ids=ids, file_key=key, created_by=actor)
    s.add(batch)
    now = utcnow()
    for c in ordered:
        c.status = CaseStatus.exported
        c.exported_at = now
    s.commit()
    return batch


def batch_file(storage: Storage, batch: ExportBatch) -> tuple[bytes, str, str]:
    if not batch.file_key:
        raise ExportError(404, f"{batch.ref} has no file")
    name = batch.file_key.rsplit("/", 1)[-1]
    mime = "application/zip" if name.endswith(".zip") else (
        "text/csv" if name.endswith(".csv") else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return storage.get(batch.file_key), mime, name

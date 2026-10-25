"""Master data as CSV, in the data/master/*.csv format the seed loads: export, and an all-or-nothing upsert
import (vendors by code, POs by po_number, PO lines by po_number + line_no).

Import guards:
- every row is validated before anything is written; any error rejects the whole file;
- an existing vendor's bank details (bank_last4, iban_hash) cannot change here: that only happens through a
  verified bank letter on vendor approval;
- a changed PO amount goes through po_ledger.amend, so it gets a po_amendments audit row (source=import).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import po_ledger
from app.models import POLine, PurchaseOrder, Vendor
from app.tenancy import require_owner, scoped

Entity = Literal["vendors", "purchase_orders", "po_lines"]

# the data/master CSV contract (backend/scripts/generate_fixtures.py writes it, app/seed.py reads it)
COLUMNS: dict[str, list[str]] = {
    "vendors": [
        "code", "legal_name", "aliases", "tax_id", "country", "email_domain", "bank_last4", "iban_hash",
        "category", "status", "default_gl", "non_po_allowed",
    ],
    "purchase_orders": ["po_number", "vendor_code", "currency", "amount", "status", "created_at", "approved_by", "notes"],
    "po_lines": ["po_number", "line_no", "description", "qty", "unit_price", "amount", "gl_code"],
}
VENDOR_STATUSES = ("approved", "pending_docs", "rejected", "inactive")
PO_STATUSES = ("open", "closed")
BANK_FIELDS = ("bank_last4", "iban_hash")


class MasterCSVError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:5]))
        self.errors = errors


@dataclass
class ImportReport:
    entity: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- formatting
def _money(v: Decimal) -> str:
    return f"{v:.2f}"


def _qty(v: Decimal) -> str:
    s = f"{v:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _write(entity: str, rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS[entity], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def export_csv(s: Session, entity: Entity) -> str:
    if entity == "vendors":
        return _write(entity, [
            {
                "code": v.code, "legal_name": v.legal_name, "aliases": "|".join(v.aliases or []),
                "tax_id": v.tax_id or "", "country": v.country or "", "email_domain": v.email_domain or "",
                "bank_last4": v.bank_last4 or "", "iban_hash": v.iban_hash or "", "category": v.category or "",
                "status": v.status, "default_gl": v.default_gl or "",
                "non_po_allowed": "true" if v.non_po_allowed else "false",
            }
            for v in s.scalars(scoped(select(Vendor), require_owner(s)).order_by(Vendor.code)).all()
        ])
    codes = {v.id: v.code for v in s.scalars(scoped(select(Vendor), require_owner(s))).all()}
    pos = s.scalars(scoped(select(PurchaseOrder), require_owner(s)).order_by(PurchaseOrder.po_number)).all()
    if entity == "purchase_orders":
        return _write(entity, [
            {
                "po_number": po.po_number, "vendor_code": codes.get(po.vendor_id or 0, ""), "currency": po.currency,
                "amount": _money(po.amount), "status": po.status,
                "created_at": po.created_at.date().isoformat() if po.created_at else "",
                "approved_by": po.approved_by or "", "notes": po.notes or "",
            }
            for po in pos
        ])
    numbers = {po.id: po.po_number for po in pos}
    lines = s.scalars(scoped(select(POLine), require_owner(s))).all()
    return _write(entity, [
        {
            "po_number": numbers[ln.po_id], "line_no": ln.line_no, "description": ln.description,
            "qty": _qty(ln.qty), "unit_price": _money(ln.unit_price), "amount": _money(ln.amount),
            "gl_code": ln.gl_code or "",
        }
        for ln in sorted(lines, key=lambda ln: (numbers[ln.po_id], ln.line_no))
    ])


# ---------------------------------------------------------------- parsing
def _rows(entity: str, text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = reader.fieldnames or []
    missing = [c for c in COLUMNS[entity] if c not in header]
    extra = [c for c in header if c not in COLUMNS[entity]]
    if missing or extra:
        raise MasterCSVError(
            [f"{entity}.csv header: " + "; ".join(
                part for part in (
                    f"missing {', '.join(missing)}" if missing else "",
                    f"unknown {', '.join(extra)}" if extra else "",
                ) if part
            ) + f" (expected {','.join(COLUMNS[entity])})"]
        )
    return [{k: (v or "").strip() for k, v in r.items() if k is not None} for r in reader]


def _dec(row: dict[str, str], key: str, errors: list[str], where: str, *, positive: bool = False) -> Decimal | None:
    try:
        v = Decimal(row[key].replace(",", ""))
    except InvalidOperation:
        errors.append(f"{where}: {key} '{row[key]}' is not a number")
        return None
    if v < 0 or (positive and v == 0):
        errors.append(f"{where}: {key} must be {'positive' if positive else 'zero or more'}")
        return None
    return v


def _required(row: dict[str, str], keys: tuple[str, ...], errors: list[str], where: str) -> bool:
    empty = [k for k in keys if not row[k]]
    if empty:
        errors.append(f"{where}: {', '.join(empty)} required")
    return not empty


def _day(v: str, errors: list[str], where: str) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.combine(datetime.fromisoformat(v).date(), time(9, 0), UTC)
    except ValueError:
        errors.append(f"{where}: created_at '{v}' is not a date (YYYY-MM-DD)")
        return None


def _set(obj: Any, values: dict[str, Any]) -> bool:
    changed = False
    for k, v in values.items():
        if getattr(obj, k) != v:
            setattr(obj, k, v)
            changed = True
    return changed


# ---------------------------------------------------------------- import
def import_csv(s: Session, entity: Entity, text: str, actor: str) -> ImportReport:
    """Validate every row, then upsert (caller commits). Raises MasterCSVError and writes nothing on any error."""
    rows = _rows(entity, text)
    if entity == "vendors":
        return _import_vendors(s, rows)
    if entity == "purchase_orders":
        return _import_pos(s, rows, actor)
    return _import_lines(s, rows)


def _dupes(keys: list[Any], label: str) -> list[str]:
    seen: set[Any] = set()
    out = []
    for k in keys:
        if k in seen:
            out.append(f"{label} {k} appears more than once")
        seen.add(k)
    return out


def _import_vendors(s: Session, rows: list[dict[str, str]]) -> ImportReport:
    errors = _dupes([r["code"] for r in rows], "vendor")
    existing = {v.code: v for v in s.scalars(scoped(select(Vendor), require_owner(s))).all()}
    parsed: list[tuple[str, dict[str, Any]]] = []
    for n, r in enumerate(rows, start=2):
        where = f"row {n} ({r['code'] or 'no code'})"
        if not _required(r, ("code", "legal_name", "status"), errors, where):
            continue
        if r["status"] not in VENDOR_STATUSES:
            errors.append(f"{where}: status '{r['status']}' is not one of {', '.join(VENDOR_STATUSES)}")
        if r["non_po_allowed"] not in ("true", "false", ""):
            errors.append(f"{where}: non_po_allowed must be true or false")
        if r["bank_last4"] and not (len(r["bank_last4"]) == 4 and r["bank_last4"].isdigit()):
            errors.append(f"{where}: bank_last4 must be 4 digits")
        if r["country"] and len(r["country"]) != 2:
            errors.append(f"{where}: country must be a 2-letter code")
        values: dict[str, Any] = {
            "legal_name": r["legal_name"], "aliases": [a for a in r["aliases"].split("|") if a],
            "tax_id": r["tax_id"] or None, "country": r["country"].upper() or None,
            "email_domain": r["email_domain"] or None, "bank_last4": r["bank_last4"] or None,
            "iban_hash": r["iban_hash"] or None, "category": r["category"] or None, "status": r["status"],
            "default_gl": r["default_gl"] or None, "non_po_allowed": r["non_po_allowed"] == "true",
        }
        v = existing.get(r["code"])
        if v is not None:
            changed = [f for f in BANK_FIELDS if getattr(v, f) != values[f]]
            if changed:
                errors.append(
                    f"{where}: {', '.join(changed)} differs from the vendor master; bank details change only "
                    "through a verified bank letter (vendor approval)"
                )
        parsed.append((r["code"], values))
    if errors:
        raise MasterCSVError(errors)
    rep = ImportReport("vendors")
    for code, values in parsed:
        v = existing.get(code)
        if v is None:
            s.add(Vendor(code=code, **values))
            rep.created += 1
        elif _set(v, values):
            rep.updated += 1
        else:
            rep.unchanged += 1
    s.flush()
    return rep


def _import_pos(s: Session, rows: list[dict[str, str]], actor: str) -> ImportReport:
    errors = _dupes([r["po_number"] for r in rows], "PO")
    vendors = {v.code: v.id for v in s.scalars(scoped(select(Vendor), require_owner(s))).all()}
    existing = {po.po_number: po for po in s.scalars(scoped(select(PurchaseOrder), require_owner(s))).all()}
    parsed: list[tuple[str, dict[str, Any]]] = []
    for n, r in enumerate(rows, start=2):
        where = f"row {n} ({r['po_number'] or 'no PO number'})"
        if not _required(r, ("po_number", "vendor_code", "currency", "amount", "status"), errors, where):
            continue
        if r["vendor_code"] not in vendors:
            errors.append(f"{where}: vendor {r['vendor_code']} does not exist (import vendors first)")
        if r["status"] not in PO_STATUSES:
            errors.append(f"{where}: status '{r['status']}' is not one of {', '.join(PO_STATUSES)}")
        if len(r["currency"]) != 3:
            errors.append(f"{where}: currency must be a 3-letter code")
        amount = _dec(r, "amount", errors, where, positive=True)
        created = _day(r["created_at"], errors, where)
        values: dict[str, Any] = {
            "vendor_id": vendors.get(r["vendor_code"]), "currency": r["currency"].upper(), "amount": amount,
            "status": r["status"], "approved_by": r["approved_by"] or None, "notes": r["notes"] or None,
        }
        if created is not None:
            values["created_at"] = created
        parsed.append((r["po_number"], values))
    if errors:
        raise MasterCSVError(errors)
    rep = ImportReport("purchase_orders")
    for number, values in parsed:
        po = existing.get(number)
        if po is None:
            s.add(PurchaseOrder(po_number=number, **values))
            rep.created += 1
            continue
        amount = values.pop("amount")
        old = po.amount
        amended = po_ledger.amend(s, po, amount, amended_by=actor, reason="Master data CSV import", source="import")
        if amended is not None:
            rep.notes.append(f"{number} amount {_money(old)} -> {_money(amount)}")
        if _set(po, values) or amended is not None:
            rep.updated += 1
        else:
            rep.unchanged += 1
    s.flush()
    return rep


def _import_lines(s: Session, rows: list[dict[str, str]]) -> ImportReport:
    errors = _dupes([(r["po_number"], r["line_no"]) for r in rows], "PO line")
    pos = {po.po_number: po.id for po in s.scalars(scoped(select(PurchaseOrder), require_owner(s))).all()}
    existing = {(ln.po_id, ln.line_no): ln for ln in s.scalars(scoped(select(POLine), require_owner(s))).all()}
    parsed: list[tuple[int, int, dict[str, Any]]] = []
    for n, r in enumerate(rows, start=2):
        where = f"row {n} ({r['po_number'] or 'no PO number'} line {r['line_no'] or '?'})"
        if not _required(r, ("po_number", "line_no", "description", "qty", "unit_price", "amount"), errors, where):
            continue
        po_id = pos.get(r["po_number"])
        if po_id is None:
            errors.append(f"{where}: PO {r['po_number']} does not exist (import purchase orders first)")
        if not r["line_no"].isdigit() or int(r["line_no"]) < 1:
            errors.append(f"{where}: line_no must be a positive whole number")
            continue
        qty = _dec(r, "qty", errors, where, positive=True)
        price = _dec(r, "unit_price", errors, where)
        amount = _dec(r, "amount", errors, where)
        if po_id is not None and None not in (qty, price, amount):
            parsed.append((po_id, int(r["line_no"]), {
                "description": r["description"], "qty": qty, "unit_price": price, "amount": amount,
                "gl_code": r["gl_code"] or None,
            }))
    if errors:
        raise MasterCSVError(errors)
    rep = ImportReport("po_lines")
    for po_id, line_no, values in parsed:
        ln = existing.get((po_id, line_no))
        if ln is None:
            s.add(POLine(po_id=po_id, line_no=line_no, **values))
            rep.created += 1
        elif _set(ln, values):
            rep.updated += 1
        else:
            rep.unchanged += 1
    s.flush()
    return rep

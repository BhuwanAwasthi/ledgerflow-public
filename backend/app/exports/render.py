"""ERP template rendering (SPEC 4.5): profile columns are sandboxed Jinja2 expressions rendered per invoice
line (grain: line) or once per invoice (grain: header). Filters: `money` (plain 2-dp number, no thousands
separator, as import files expect) and `fmt_date` (the profile's `date_format`).

Everything here is pure: rows in, rows / bytes out. The template agent and the export service share it.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from jinja2 import Undefined
from jinja2.sandbox import SandboxedEnvironment
from openpyxl import Workbook

from app.config_loader import ErpProfile

Row = dict[str, str]


def money(value: Any) -> str:
    if value is None or isinstance(value, Undefined) or value == "":
        return ""
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return str(value)


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def make_env(profile: ErpProfile) -> SandboxedEnvironment:
    env = SandboxedEnvironment(autoescape=False, finalize=lambda v: "" if v is None else v)
    fmt = profile.date_format or "%Y-%m-%d"

    def fmt_date(value: Any) -> str:
        if value is None or isinstance(value, Undefined) or value == "":
            return ""
        d = _to_date(value)
        return d.strftime(fmt) if d else str(value)

    env.filters["money"] = money
    env.filters["fmt_date"] = fmt_date
    return env


def render_string(env: SandboxedEnvironment, template: str, ctx: dict[str, Any]) -> str:
    return env.from_string(template).render(**ctx).strip()


@dataclass
class Rendered:
    rows: list[Row]
    missing: list[dict[str, Any]] = field(default_factory=list)  # required cells left empty

    @property
    def valid(self) -> bool:
        return not self.missing


def render_rows(profile: ErpProfile, ctx: dict[str, Any]) -> Rendered:
    """`ctx` holds case, vendor, inv, po, lines and version. For grain `line` every line is rendered with
    `line` and `loop_index`; for grain `header` once with `line` = the first line (or None)."""
    env = make_env(profile)
    compiled = [(c, env.from_string(c.value)) for c in profile.columns]
    lines: list[dict[str, Any]] = ctx.get("lines") or []
    units = list(enumerate(lines)) if profile.grain == "line" else [(0, lines[0] if lines else None)]
    if profile.grain == "line" and not units:
        units = [(0, None)]
    out = Rendered(rows=[])
    for i, line in units:
        row: Row = {}
        for col, tpl in compiled:
            value = tpl.render(**ctx, line=line, loop_index=i).strip()
            row[col.header] = value
            if col.required and value == "":
                out.missing.append({"row": len(out.rows), "column": col.header})
        out.rows.append(row)
    return out


def headers(profile: ErpProfile) -> list[str]:
    return [c.header for c in profile.columns]


def diff_rows(prev: list[Row] | None, new: list[Row]) -> dict[str, Any] | None:
    """Cell-level diff between two template versions (None for the first version)."""
    if prev is None:
        return None
    changed: list[dict[str, Any]] = []
    for i in range(min(len(prev), len(new))):
        for col in dict.fromkeys([*prev[i], *new[i]]):
            old, cur = prev[i].get(col, ""), new[i].get(col, "")
            if old != cur:
                changed.append({"row": i, "column": col, "old": old, "new": cur})
    return {
        "changed": changed,
        "added_rows": list(range(len(prev), len(new))),
        "removed_rows": list(range(len(new), len(prev))),
        "unchanged": not changed and len(prev) == len(new),
    }


def to_bytes(profile: ErpProfile, rows: list[Row]) -> tuple[bytes, str]:
    """(file bytes, media type) in the profile's format, header row once."""
    cols = headers(profile)
    if profile.file_format == "xlsx":
        wb = Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "AP Bills"
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c, "") for c in cols])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    sio = io.StringIO()
    w = csv.writer(sio, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    return sio.getvalue().encode("utf-8"), "text/csv"

"""`make fixtures`: data/scenarios.yaml -> data/master/*.csv, data/fixtures/*.pdf, backend/tests/llm_fixtures/*.json.

Deterministic: reportlab runs in invariant mode and scan noise comes from a seeded RNG, so the same YAML
always yields byte-identical files. Run from backend/: `uv run python -m scripts.generate_fixtures`.

Each vendor gets its own invoice look (layout family, font, colour, labels, date and money formats) so
extraction is not a single-template exercise. `render: scan` rasterises the clean layout at 120 dpi,
rotates it 0.8 deg, adds Gaussian noise and a slight blur, and saves an image-only PDF.

LLM fixture format (one file per scenario and agent, read by FakeLLM):
    {"scenario": id, "agent": id, "documents": [filenames],
     "calls": [{"action": name, "schema": <llm_schemas model>, "when": {ctx_key: value}, "output": {...}}]}
A call matches when every `when` key equals the caller's context value; an empty `when` is the default.
Context keys used: document (filename), pages ([from, to]), invoice_number, decision, remark.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageChops, ImageFilter
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from app.scenarios import InvoiceSpec, Scenarios, ScenarioSpec, load_scenarios, money
from app.settings import BACKEND_DIR, REPO_ROOT

PAGE_W, PAGE_H = letter
SCAN_DPI = 120
SCAN_ROTATE_DEG = 0.8
SYNTHETIC_FOOTER = "Synthetic test document generated for the LedgerFlow demo. All companies are fictional."


# --------------------------------------------------------------------------------------------------
# Vendor styles
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Style:
    layout: str  # classic | banner | ledger | sidebar
    font: str
    bold: str
    color: str
    title: str
    date_fmt: str
    money_suffix: bool = False  # "1,250.00 USD" instead of "$1,250.00"
    labels: dict[str, str] = field(default_factory=dict)
    zebra: bool = False


DEFAULT_LABELS = {
    "number": "Invoice No.",
    "date": "Invoice Date",
    "due": "Due Date",
    "terms": "Terms",
    "po": "PO Number",
    "ref": "Reference",
    "bill_to": "Bill To",
    "subtotal": "Subtotal",
    "tax": "Sales Tax",
    "total": "Total Due",
    "desc": "Description",
    "qty": "Qty",
    "uom": "Unit",
    "price": "Unit Price",
    "amount": "Amount",
}


def _labels(**over: str) -> dict[str, str]:
    return {**DEFAULT_LABELS, **over}


STYLES: dict[str, Style] = {
    "V001": Style("classic", "Helvetica", "Helvetica-Bold", "#1F3A93", "INVOICE", "%b %d, %Y", labels=_labels()),
    "V002": Style(
        "banner", "Helvetica", "Helvetica-Bold", "#0F766E", "Invoice", "%Y-%m-%d",
        labels=_labels(number="Invoice #", po="Purchase Order", total="Amount Due", tax="Tax"), zebra=True,
    ),
    "V003": Style(
        "ledger", "Courier", "Courier-Bold", "#14532D", "FREIGHT INVOICE", "%d %b %Y", money_suffix=True,
        labels=_labels(number="INVOICE NO", date="DATE", due="DUE", terms="TERMS", po="CUST PO",
                       ref="REF", bill_to="BILL TO", subtotal="SUBTOTAL", tax="TAX", total="TOTAL USD",
                       desc="SERVICE", qty="QTY", uom="UOM", price="RATE", amount="CHARGES"),
    ),
    "V004": Style(
        "sidebar", "Helvetica", "Helvetica-Bold", "#C2410C", "Service Invoice", "%B %d, %Y",
        labels=_labels(number="Invoice Number", po="Your PO", total="Balance Due"),
    ),
    "V005": Style(
        "classic", "Times-Roman", "Times-Bold", "#166534", "Invoice", "%d %B %Y",
        labels=_labels(number="Invoice Ref.", date="Date of Issue", po="Customer Order No.", total="Invoice Total"),
    ),
    "V006": Style(
        "banner", "Times-Roman", "Times-Bold", "#5B21B6", "INVOICE", "%B %d, %Y",
        labels=_labels(number="Invoice", date="Issued", due="Payment Due", ref="Your Ref"), zebra=False,
    ),
    "V007": Style("ledger", "Courier", "Courier-Bold", "#334155", "INVOICE", "%Y-%m-%d", labels=_labels()),
    "NEW": Style(
        "sidebar", "Helvetica", "Helvetica-Bold", "#BE185D", "INVOICE", "%b %d, %Y",
        labels=_labels(number="Invoice #", total="Total"),
    ),
}

BANK_ACCOUNT_LABEL = {
    "classic": "Account ending {last4}",
    "banner": "Acct ****{last4}",
    "ledger": "ACCT XXXX-XXXX-{last4}",
    "sidebar": "Account no. ******{last4}",
}


@dataclass
class VendorInfo:
    code: str  # style key
    name: str
    address: str
    email: str
    tax_id: str
    bank_name: str


def vendor_info(sc: Scenarios, scenario: ScenarioSpec, inv: InvoiceSpec) -> VendorInfo:
    if inv.vendor:
        v = sc.vendor(inv.vendor)
        return VendorInfo(v.code, v.legal_name, v.address or "", f"billing@{v.email_domain}", v.tax_id,
                          v.bank_name or "")
    nv = scenario.new_vendor
    assert nv is not None, f"{scenario.id}: invoice without vendor code needs new_vendor"
    return VendorInfo("NEW", inv.vendor_name_printed or nv.legal_name, nv.address or "",
                      f"billing@{nv.email_domain}", nv.tax_id, nv.bank_name or "")


def initials(name: str) -> str:
    words = [w for w in name.replace(",", " ").split() if w[0].isalpha()]
    return "".join(w[0] for w in words[:2]).upper()


def fmt_qty(q: Decimal) -> str:
    return f"{q:,.0f}" if q == q.to_integral_value() else f"{q.normalize():,}"


# --------------------------------------------------------------------------------------------------
# PDF drawing with bounding-box capture
# --------------------------------------------------------------------------------------------------


class Pen:
    """Thin wrapper over a reportlab canvas that records normalised bboxes of tagged values."""

    def __init__(self, c: Canvas, style: Style):
        self.c = c
        self.s = style
        self.page = 1
        self.boxes: dict[str, dict[str, Any]] = {}

    def text(self, x: float, y: float, s: str, size: float = 9, *, bold: bool = False, color: Any = None,
             align: str = "left", fld: str | None = None) -> float:
        font = self.s.bold if bold else self.s.font
        w = stringWidth(s, font, size)
        x0 = x if align == "left" else x - w if align == "right" else x - w / 2
        c = self.c
        c.setFont(font, size)
        c.setFillColor(color if color is not None else HexColor("#111827"))
        c.drawString(x0, y, s)
        if fld:
            self.boxes[fld] = {
                "page": self.page,
                "bbox": [round(x0 / PAGE_W, 4), round((PAGE_H - y - size * 0.78) / PAGE_H, 4),
                         round((x0 + w) / PAGE_W, 4), round((PAGE_H - y + size * 0.24) / PAGE_H, 4)],
            }
        return w

    def money(self, d: Decimal) -> str:
        return f"{d:,.2f} USD" if self.s.money_suffix else f"${d:,.2f}"


def _meta_rows(st: Style, inv: InvoiceSpec, number: str | None) -> list[tuple[str, str, str | None]]:
    rows: list[tuple[str, str, str | None]] = []
    lb = st.labels
    if number:
        rows.append((lb["number"], number, "invoice_number"))
    if inv.invoice_date:
        rows.append((lb["date"], inv.invoice_date.strftime(st.date_fmt), "invoice_date"))
    if inv.due_date:
        rows.append((lb["due"], inv.due_date.strftime(st.date_fmt), None))
    if inv.terms:
        rows.append((lb["terms"], inv.terms, None))
    if inv.po_reference:
        rows.append((lb["po"], inv.po_reference, "po_reference"))
    for ref in inv.other_references:
        rows.append((lb["ref"], ref, None))
    return rows


def draw_invoice(pen: Pen, inv: InvoiceSpec, v: VendorInfo, buyer: dict[str, str], number: str | None) -> None:
    st, c = pen.s, pen.c
    accent = HexColor(st.color)
    grey = HexColor("#6B7280")
    meta = _meta_rows(st, inv, number)
    addr = [a.strip() for a in v.address.split(",")]
    addr_lines = [", ".join(addr[:-2]), ", ".join(addr[-2:])] if len(addr) > 2 else [v.address]
    vendor_lines = [*addr_lines, v.email, f"Tax ID {v.tax_id}"]
    bill_lines = [buyer["name"], *[a.strip() for a in buyer["address"].split(",", 1)], buyer["ap_email"]]
    left, right = 50.0, PAGE_W - 50

    if st.layout == "classic":
        c.setFillColor(accent)
        c.roundRect(left, 700, 44, 44, 6, stroke=0, fill=1)
        pen.text(left + 22, 716, initials(v.name), 16, bold=True, color=white, align="center")
        pen.text(left + 56, 732, v.name, 14, bold=True, fld="vendor_name")
        for i, ln in enumerate(vendor_lines):
            pen.text(left + 56, 718 - i * 11, ln, 8.5, color=grey)
        pen.text(right, 728, st.title, 24, bold=True, color=accent, align="right")
        y = 700
        for lab, val, fld in meta:
            pen.text(right - 150, y, lab, 9, color=grey)
            pen.text(right, y, val, 9, bold=True, align="right", fld=fld)
            y -= 14
        pen.text(left, 640, st.labels["bill_to"].upper(), 8, bold=True, color=accent)
        for i, ln in enumerate(bill_lines):
            pen.text(left, 627 - i * 11, ln, 9)
        table_top = min(y, 585) - 15
    elif st.layout == "banner":
        c.setFillColor(accent)
        c.rect(0, PAGE_H - 100, PAGE_W, 100, stroke=0, fill=1)
        pen.text(left, PAGE_H - 50, v.name, 18, bold=True, color=white, fld="vendor_name")
        pen.text(left, PAGE_H - 66, " | ".join(addr_lines), 8.5, color=white)
        pen.text(left, PAGE_H - 78, f"{v.email}  |  Tax ID {v.tax_id}", 8.5, color=white)
        pen.text(right, PAGE_H - 52, st.title, 22, bold=True, color=white, align="right")
        box_w = (right - left) / 4
        y = PAGE_H - 140
        for i, (lab, val, fld) in enumerate(meta):
            col, row = i % 4, i // 4
            bx, by = left + col * box_w, y - row * 42
            c.setStrokeColor(HexColor("#D1D5DB"))
            c.rect(bx + 2, by - 14, box_w - 4, 36, stroke=1, fill=0)
            pen.text(bx + 8, by + 10, lab.upper(), 7, color=grey)
            pen.text(bx + 8, by - 6, val, 10, bold=True, fld=fld)
        rows = (len(meta) + 3) // 4
        by = y - rows * 42 - 14
        pen.text(left, by, st.labels["bill_to"], 9, bold=True, color=accent)
        for i, ln in enumerate(bill_lines):
            pen.text(left, by - 13 - i * 11, ln, 9)
        table_top = by - 13 - len(bill_lines) * 11 - 22
    elif st.layout == "ledger":
        pen.text(left, 735, st.title, 18, bold=True, color=accent)
        pen.text(right, 740, v.name.upper(), 11, bold=True, align="right", fld="vendor_name")
        for i, ln in enumerate(vendor_lines):
            pen.text(right, 727 - i * 10, ln, 8, align="right", color=grey)
        c.setStrokeColor(accent)
        c.setLineWidth(1.5)
        c.line(left, 670, right, 670)
        y = 652
        for lab, val, fld in meta:
            pen.text(left, y, f"{lab}:", 9, bold=True)
            pen.text(left + 95, y, val, 9, fld=fld)
            y -= 13
        pen.text(330, 652, f"{st.labels['bill_to']}:", 9, bold=True)
        for i, ln in enumerate(bill_lines):
            pen.text(330, 639 - i * 11, ln, 9)
        c.line(left, min(y, 595) - 2, right, min(y, 595) - 2)
        table_top = min(y, 595) - 22
    else:  # sidebar
        side_w = 160
        c.setFillColor(accent)
        c.rect(0, 0, side_w, PAGE_H, stroke=0, fill=1)
        pen.text(24, PAGE_H - 70, initials(v.name), 30, bold=True, color=white)
        words, lines_, cur = v.name.split(), [], ""
        for w in words:
            if stringWidth(f"{cur} {w}".strip(), st.bold, 12) > side_w - 40:
                lines_.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        lines_.append(cur)
        yy = PAGE_H - 100
        for i, ln in enumerate(lines_):
            pen.text(24, yy - i * 15, ln, 12, bold=True, color=white, fld="vendor_name" if i == 0 else None)
        if len(lines_) > 1:  # bbox spans the wrapped name
            b = pen.boxes["vendor_name"]["bbox"]
            b[2] = round(max(b[2], (24 + max(stringWidth(x, st.bold, 12) for x in lines_)) / PAGE_W), 4)
            b[3] = round((PAGE_H - (yy - (len(lines_) - 1) * 15) + 12 * 0.24) / PAGE_H, 4)
        yy -= len(lines_) * 15 + 10
        for ln in vendor_lines:
            pen.text(24, yy, ln, 7.5, color=white)
            yy -= 11
        left = side_w + 25
        pen.text(left, 730, st.title, 22, bold=True, color=accent)
        y = 700
        for lab, val, fld in meta:
            pen.text(left, y, lab, 9, color=grey)
            pen.text(left + 100, y, val, 9, bold=True, fld=fld)
            y -= 14
        pen.text(400, 700, st.labels["bill_to"], 9, bold=True, color=accent)
        for i, ln in enumerate(bill_lines):
            pen.text(400, 687 - i * 11, ln, 9)
        table_top = min(y, 640) - 20

    # --- line table ---
    has_uom = any(ln.uom for ln in inv.lines)
    lb = st.labels
    cols = [(lb["desc"], left + 6, "left")]
    if has_uom:
        cols.append((lb["uom"], right - 225, "right"))
    cols += [(lb["qty"], right - 175, "right"), (lb["price"], right - 95, "right"), (lb["amount"], right - 6, "right")]
    row_h = 20
    if st.layout == "ledger":
        c.setStrokeColor(HexColor("#111827"))
        c.setLineWidth(0.6)
        c.line(left, table_top + 14, right, table_top + 14)
        c.line(left, table_top - 6, right, table_top - 6)
        head_color: Any = None
    else:
        c.setFillColor(accent if st.layout != "banner" else HexColor("#E5E7EB"))
        c.rect(left, table_top - 6, right - left, row_h, stroke=0, fill=1)
        head_color = white if st.layout != "banner" else None
    for name, x, al in cols:
        pen.text(x, table_top, name, 8.5, bold=True, color=head_color, align=al)
    y = table_top - row_h
    for i, ln in enumerate(inv.lines):
        if st.zebra and i % 2 == 1:
            c.setFillColor(HexColor("#F3F4F6"))
            c.rect(left, y - 6, right - left, row_h, stroke=0, fill=1)
        vals = [ln.description]
        if has_uom:
            vals.append(ln.uom or "")
        vals += [fmt_qty(ln.qty), f"{ln.unit_price:,.2f}", f"{ln.amount:,.2f}"]
        for (_, x, al), val in zip(cols, vals, strict=True):
            pen.text(x, y, val, 9, align=al)
        if st.layout == "classic":
            c.setStrokeColor(HexColor("#E5E7EB"))
            c.setLineWidth(0.5)
            c.line(left, y - 6, right, y - 6)
        y -= row_h
    if st.layout == "ledger":
        c.line(left, y + 12, right, y + 12)

    # --- totals ---
    y -= 8
    rate = inv.tax_rate_pct.normalize()
    rate_s = f"{rate:f}"
    totals = [
        (lb["subtotal"], pen.money(inv.subtotal), "subtotal", False),
        (f"{lb['tax']} ({rate_s}%)", pen.money(inv.tax), "tax_total", False),
        (lb["total"], pen.money(inv.total), "total", True),
    ]
    for lab, val, fld, strong in totals:
        if strong:
            c.setStrokeColor(accent)
            c.setLineWidth(1)
            c.line(right - 210, y + 13, right, y + 13)
            y -= 4
        pen.text(right - 210, y, lab, 10 if strong else 9, bold=strong, color=accent if strong else None)
        pen.text(right - 6, y, val, 11 if strong else 9, bold=strong, align="right", fld=fld)
        y -= 16

    # --- remittance + notes ---
    y = min(y - 30, 190)
    if inv.footer_note:
        pen.text(left, y + 24, inv.footer_note, 9.5, bold=True, color=HexColor("#B91C1C"))
    if inv.bank_last4:
        pen.text(left, y, "Remit payment to", 8, bold=True, color=accent)
        acct = BANK_ACCOUNT_LABEL[st.layout].format(last4=inv.bank_last4)
        pen.text(left, y - 12, f"{v.bank_name}  |  {acct}", 9)
        pen.text(left, y - 24, f"Account name: {v.name}", 8.5, color=grey)
    pen.text(left, y - 50, "Thank you for your business.", 8.5, color=grey)
    pen.text(PAGE_W / 2 + (80 if st.layout == "sidebar" else 0), 24, SYNTHETIC_FOOTER, 6.5,
             color=HexColor("#9CA3AF"), align="center")


def _canvas(buf: io.BytesIO, title: str) -> Canvas:
    c = Canvas(buf, pagesize=letter, invariant=1)
    c.setTitle(title)
    c.setAuthor("LedgerFlow fixture generator")
    c.setCreator("LedgerFlow fixture generator")
    return c


def render_invoices(sc: Scenarios, scenario: ScenarioSpec, number_override: str | None = None
                    ) -> tuple[bytes, list[dict[str, dict[str, Any]]]]:
    """One page per invoice. Returns the PDF bytes and the bboxes of tagged fields per page."""
    buf = io.BytesIO()
    c = _canvas(buf, scenario.title)
    boxes: list[dict[str, dict[str, Any]]] = []
    buyer = sc.buyer.model_dump()
    for page_no, inv in enumerate(sc.invoices(scenario), start=1):
        v = vendor_info(sc, scenario, inv)
        pen = Pen(c, STYLES[v.code])
        pen.page = page_no
        draw_invoice(pen, inv, v, buyer, number_override or inv.invoice_number)
        boxes.append(pen.boxes)
        c.showPage()
    c.save()
    return buf.getvalue(), boxes


def render_w9(sc: Scenarios, scenario: ScenarioSpec) -> bytes:
    nv = scenario.new_vendor
    assert nv is not None
    buf = io.BytesIO()
    c = _canvas(buf, f"Taxpayer identification - {nv.legal_name}")
    pen = Pen(c, Style("classic", "Helvetica", "Helvetica-Bold", "#111827", "", "%Y-%m-%d"))
    pen.text(50, 735, "Form W-9 (synthetic demo copy)", 16, bold=True)
    pen.text(50, 718, "Request for Taxpayer Identification Number and Certification", 10)
    c.setLineWidth(1)
    c.line(50, 705, PAGE_W - 50, 705)
    addr = nv.address or ""
    street, _, city = addr.partition(", ")
    fields = [
        ("1  Name (as shown on your income tax return)", nv.legal_name),
        ("2  Business name / disregarded entity name", nv.legal_name),
        ("3  Federal tax classification", "Limited liability company"),
        ("5  Address (number, street, and apt. or suite no.)", street),
        ("6  City, state, and ZIP code", city),
        ("Part I  Employer identification number (EIN)", nv.tax_id),
    ]
    y = 680
    for lab, val in fields:
        pen.text(50, y, lab, 8, color=HexColor("#4B5563"))
        c.setStrokeColor(HexColor("#9CA3AF"))
        c.rect(50, y - 26, PAGE_W - 100, 20, stroke=1, fill=0)
        pen.text(56, y - 20, val, 11)
        y -= 46
    pen.text(50, y - 5, "Part II  Certification", 10, bold=True)
    pen.text(50, y - 20, "Under penalties of perjury, I certify that the number shown on this form is my correct", 8.5)
    pen.text(50, y - 31, "taxpayer identification number. (Demo text: this is not a real tax form.)", 8.5)
    pen.text(50, y - 70, "Signature:  M. Alvarez, Managing Member", 10)
    pen.text(360, y - 70, "Date:  2026-10-06", 10)
    pen.text(PAGE_W / 2, 24, SYNTHETIC_FOOTER, 6.5, color=HexColor("#9CA3AF"), align="center")
    c.showPage()
    c.save()
    return buf.getvalue()


def render_bank_letter(sc: Scenarios, scenario: ScenarioSpec, holder: str, last4: str) -> bytes:
    nv = scenario.new_vendor
    assert nv is not None
    bank = nv.bank_name or "Bank"
    buf = io.BytesIO()
    c = _canvas(buf, f"Bank account confirmation - {holder}")
    pen = Pen(c, Style("classic", "Times-Roman", "Times-Bold", "#1E3A8A", "", "%B %d, %Y"))
    accent = HexColor("#1E3A8A")
    c.setFillColor(accent)
    c.rect(0, PAGE_H - 80, PAGE_W, 80, stroke=0, fill=1)
    pen.text(50, PAGE_H - 48, bank.upper(), 20, bold=True, color=white)
    pen.text(50, PAGE_H - 64, "Commercial Banking  |  1200 Fourth Ave, Seattle, WA 98101", 9, color=white)
    y = 660
    for ln in ["October 6, 2026", "", "To: Accounts Payable, " + sc.buyer.name, sc.buyer.address, "",
               "Re: Confirmation of account details"]:
        pen.text(50, y, ln, 11, bold=ln.startswith("Re:"))
        y -= 16
    y -= 8
    body = [
        f"This letter confirms that the account described below is held with {bank} in the name of",
        f"{holder} and is in good standing.",
    ]
    for ln in body:
        pen.text(50, y, ln, 11)
        y -= 16
    y -= 10
    for lab, val in [("Account holder", holder), ("Account number", f"XXXXXX{last4}"),
                     ("Routing number (ABA)", "125000000 (demo)"), ("Account type", "Business checking")]:
        pen.text(70, y, f"{lab}:", 11, bold=True)
        pen.text(230, y, val, 11)
        y -= 18
    y -= 30
    pen.text(50, y, "Sincerely,", 11)
    pen.text(50, y - 40, "J. Okafor, Branch Operations Manager", 11, bold=True)
    pen.text(50, y - 56, f"{bank}  (synthetic demo letter)", 10)
    pen.text(PAGE_W / 2, 24, SYNTHETIC_FOOTER, 6.5, color=HexColor("#9CA3AF"), align="center")
    c.showPage()
    c.save()
    return buf.getvalue()


def rasterise(pdf: bytes, seed: str) -> bytes:
    """Image-only "scan": 120 dpi greyscale, 0.8 deg skew, Gaussian noise, slight blur, JPEG pages."""
    rng = random.Random(seed)
    src = pymupdf.open(stream=pdf, filetype="pdf")
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=SCAN_DPI, colorspace=pymupdf.csGRAY, alpha=False)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        img = img.rotate(SCAN_ROTATE_DEG, resample=Image.Resampling.BICUBIC, fillcolor=255)
        n = img.width * img.height
        noise = bytes(max(0, min(255, int(128 + rng.gauss(0, 14)))) for _ in range(n))
        img = ImageChops.add(img, Image.frombytes("L", img.size, noise), scale=1.0, offset=-128)
        img = img.filter(ImageFilter.GaussianBlur(0.6)).point(lambda p: int(p * 0.9 + 12))
        jpg = io.BytesIO()
        img.save(jpg, "JPEG", quality=72)
        dst = out.new_page(width=page.rect.width, height=page.rect.height)
        dst.insert_image(dst.rect, stream=jpg.getvalue())
    out.set_metadata({"title": "Scanned document", "producer": "LedgerFlow fixture generator"})
    return out.tobytes(garbage=3, deflate=True, no_new_id=True)


# --------------------------------------------------------------------------------------------------
# Master data CSVs
# --------------------------------------------------------------------------------------------------


def _write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_master(sc: Scenarios, master_dir: Path) -> None:
    _write_csv(master_dir / "gl_accounts.csv", ["code", "name", "category"],
               [[g.code, g.name, g.category] for g in sc.gl_accounts])
    _write_csv(
        master_dir / "vendors.csv",
        ["code", "legal_name", "aliases", "tax_id", "country", "email_domain", "bank_last4", "iban_hash",
         "category", "status", "default_gl", "non_po_allowed"],
        [[v.code, v.legal_name, "|".join(v.aliases), v.tax_id, v.country, v.email_domain, v.bank_last4, "",
          v.category, v.status, v.default_gl, str(v.non_po_allowed).lower()] for v in sc.vendors],
    )
    _write_csv(
        master_dir / "purchase_orders.csv",
        ["po_number", "vendor_code", "currency", "amount", "status", "created_at", "approved_by", "notes"],
        [[p.po_number, p.vendor, "USD", f"{p.amount:.2f}", "open", p.created_at or "", p.approved_by or "",
          p.notes or ""] for p in sc.purchase_orders],
    )
    _write_csv(
        master_dir / "po_lines.csv",
        ["po_number", "line_no", "description", "qty", "unit_price", "amount", "gl_code"],
        [[p.po_number, ln.line_no, ln.description, fmt_qty(ln.qty).replace(",", ""), f"{ln.unit_price:.2f}",
          f"{ln.amount:.2f}", ln.gl_code] for p in sc.purchase_orders for ln in p.lines],
    )
    _write_csv(
        master_dir / "grns.csv",
        ["po_number", "line_no", "qty_received", "received_at"],
        [[g.po_number, g.line_no, fmt_qty(g.qty_received).replace(",", ""), g.received_at or ""]
         for g in sc.grns],
    )


# --------------------------------------------------------------------------------------------------
# Expected LLM outputs (FakeLLM fixtures)
# --------------------------------------------------------------------------------------------------

CLEAN_CONF, SCAN_CONF = Decimal("0.98"), Decimal("0.78")

# (scenario, po_number) -> (line_similarity, [po_line_no per invoice line], reason)
PO_JUDGEMENTS: dict[tuple[str, str], tuple[float, list[int | None], str]] = {
    ("S01_happy", "PO-1002"): (0.99, [1, 2, 3], "All three lines (paper, toner, chairs) match PO lines 1-3 with the same quantities."),
    ("S02a_split_part1", "PO-1001"): (0.93, [1], "Tranche 1 of the annual Brightline Analytics licence matches PO line 1 (60 of 100 seats)."),
    ("S02b_split_part2", "PO-1001"): (0.72, [1, None], "Tranche 2 of the licence matches PO line 1; the premium support add-on is not on the PO."),
    ("S03_implied_po", "PO-1004"): (0.95, [1], "40 hours of process improvement consulting match line 1, and quote Q-77 on the invoice is the quote this PO was raised from."),
    ("S03_implied_po", "PO-1006"): (0.08, [None], "A one-day leadership workshop does not correspond to 40 hours of consulting."),
    ("S04_duplicate_scan", "PO-1002"): (0.99, [1, 2, 3], "All three lines (paper, toner, chairs) match PO lines 1-3 with the same quantities."),
    ("S06_bank_change", "PO-1005"): (0.99, [1], "2,000 corrugated shipping boxes match PO line 1 exactly."),
    ("S08_price_variance", "PO-1003"): (0.90, [1], "LTL freight Chicago to Dallas matches PO line 1; the description adds a fuel surcharge."),
    ("S09_missing_fields", "PO-1003"): (0.98, [1], "One LTL shipment Chicago to Dallas matches PO line 1."),
    ("S10_bundle", "PO-1007"): (0.99, [1], "30 boxes of whiteboard markers match PO line 1."),
    ("S10_bundle", "PO-1003"): (0.98, [1], "One LTL shipment Chicago to Dallas matches PO line 1."),
}

# scenario -> (gl_code, department, confidence, reason per line)
GL_SUGGESTIONS: dict[str, tuple[str, str | None, float, list[str]]] = {
    "S05_unknown_vendor": ("6600", "Marketing", 0.88, [
        "Trade show banners are marketing print material.",
        "Brochure printing is marketing print material.",
    ]),
    "S07_non_po": ("6100", "Facilities", 0.95, [
        "HVAC maintenance is facilities maintenance, the vendor's default account.",
        "Air filter replacement is part of HVAC maintenance.",
    ]),
}

# scenario -> {decision or "invoice_number|decision": (summary, next_step)}
EXPLANATIONS: dict[str, dict[str, tuple[str, str]]] = {
    "S01_happy": {"auto_approve": (
        ("Auto-approved: Acme Office Supplies invoice INV-A-2207 for USD 4,779.24 matches PO-1002 line for line "
        "(subtotal 4,415.00), and every item was received in full. The vendor, its bank account and the "
        "prices all match our records."),
        "The ERP template is final and ready to export.")},
    "S02a_split_part1": {"auto_approve": (
        ("Auto-approved: Brightline Software invoice BL-0091 for USD 6,000.00 bills 60 of the 100 licences on "
        "PO-1001, leaving 4,000.00 on the PO. The vendor, bank account and unit price all match."),
        "The ERP template is final and ready to export.")},
    "S02b_split_part2": {
        "needs_review": (
            ("Needs review: Brightline Software invoice BL-0107 for USD 4,300.00 is 300.00 (7.5%) over the "
            "4,000.00 left on PO-1001, above the 2% tolerance. It also bills a Premium support add-on that is "
            "not on the PO."),
            "Approve if procurement amended the PO, or add a remark with the correction."),
        "auto_approve": (
            ("After your changes invoice BL-0107 passes every check: the PO-1001 amendment to 10,500.00 covers "
            "the 4,300.00 subtotal, and the Premium support line is accepted and coded to 6410 Software Support."),
            "Review the updated template (v2) and approve to finalise it."),
    },
    "S03_implied_po": {"needs_review": (
        ("Needs review: Fulcrum Consulting invoice FCG-311 for USD 6,000.00 quotes no PO number. The best match "
        "is PO-1004, raised from quote Q-77 for the same 40 hours at 150.00; we pre-linked it, "
        "but an inferred PO always needs a person to confirm."),
        "Confirm PO-1004 and approve, or link a different PO.")},
    "S04_duplicate_scan": {"reject": (
        ("Rejected: this scanned Acme Office Supplies invoice INV A2207 for USD 4,779.24 is a duplicate of "
        "INV-A-2207, which we already received for the same amount."),
        "We drafted a note to the vendor; nothing will be paid twice.")},
    "S05_unknown_vendor": {"needs_review": (
        ("Needs review: Nova Print Studio LLC is a newly onboarded vendor and invoice NPS-0042 for USD 1,277.35 "
        "has no PO. We suggest coding both lines to 6600 Printing and Marketing."),
        "Confirm the coding with the budget owner and approve.")},
    "S06_bank_change": {"on_hold": (
        ("On hold: Evergreen Packaging invoice EP-5531 for USD 2,381.50 asks for payment to an account ending "
        "9910, but our vendor master has 7788. Changed bank details are a common payment-fraud pattern, so we "
        "will not pay until the change is verified."),
        "We drafted a request for a signed bank letter, which we will confirm by phone with a known contact.")},
    "S07_non_po": {"needs_review": (
        ("Needs review: Delta Facilities Services invoice DF-2026-10 for USD 2,150.00 has no PO. We suggest "
        "coding it to 6100 Facilities Maintenance, the vendor's usual account; non-PO spend needs "
        "budget-owner approval."),
        "Approve once the budget owner agrees.")},
    "S08_price_variance": {"needs_review": (
        ("Needs review: Coastal Logistics invoice CL-778 for USD 2,620.00 charges 1,310.00 per shipment against "
        "1,250.00 on PO-1003, 4.8% and 60.00 per unit higher, beyond the 2% and 50.00 tolerance."),
        "Approve the new rate, or ask the vendor for a credit note.")},
    "S09_missing_fields": {"on_hold": (
        ("On hold: this scanned Coastal Logistics invoice for USD 1,250.00 has no invoice number and no invoice "
        "date, and we need both before it can be processed."),
        "We drafted a request asking the vendor for a corrected copy.")},
    "S10_bundle": {
        "INV-A-2240|auto_approve": (
            ("Auto-approved: Acme Office Supplies invoice INV-A-2240 for USD 389.70 matches PO-1007 (30 boxes "
            "of whiteboard markers, all received), and the vendor and bank details match."),
            "The ERP template is final and ready to export."),
        "CL-781|auto_approve": (
            ("Auto-approved: Coastal Logistics invoice CL-781 for USD 1,250.00 bills one shipment on PO-1003 at "
            "the PO price, and the vendor and bank details match."),
            "The ERP template is final and ready to export."),
    },
}

SIGNATURE = "Accounts Payable, Demo Co."

COMMS: dict[str, tuple[str, str, str]] = {  # scenario -> (action, subject, body)
    "S04_duplicate_scan": ("draft_rejection", "Invoice INV A2207: duplicate of INV-A-2207", (
        "Hello Acme Office Supplies team,\n\n"
        "We received a scanned copy of invoice INV A2207 for USD 4,779.24. It matches invoice INV-A-2207, "
        "dated 2 October 2026, which we have already received for the same amount, so we have closed this copy "
        "as a duplicate.\n\n"
        "If it was sent twice by mistake, no action is needed. If it is a different charge, please reply with "
        "a corrected invoice that has its own number.\n\n"
        f"Kind regards,\n{SIGNATURE}")),
    "S05_unknown_vendor": ("draft_request", "Invoice NPS-0042: vendor set-up documents needed", (
        "Hello Nova Print Studio team,\n\n"
        "Thank you for invoice NPS-0042 for USD 1,277.35. You are not yet set up as a supplier in our system, "
        "so before we can process it we need two documents:\n\n"
        "1. A completed and signed W-9 for Nova Print Studio LLC.\n"
        "2. A letter from your bank, on bank letterhead, confirming the account holder name and the account "
        "the invoice asks us to pay.\n\n"
        "Please upload both through the secure link below; no login is needed. Once we have verified them we "
        "will continue with your invoice.\n\n"
        f"Kind regards,\n{SIGNATURE}")),
    "S06_bank_change": ("draft_request", "Invoice EP-5531: please confirm your new bank details", (
        "Hello Evergreen Packaging team,\n\n"
        "Thank you for invoice EP-5531 for USD 2,381.50. The invoice asks us to pay an account ending 9910, "
        "which differs from the account we hold for you (ending 7788).\n\n"
        "To protect both of us from payment fraud, please send a signed letter on your bank's letterhead "
        "confirming the account holder name and the new account details. You can upload it through the secure "
        "link below; no login is needed. We will also confirm the change by phone with a contact we already "
        "know before updating our records.\n\n"
        "We will continue with the invoice as soon as the change is verified.\n\n"
        f"Kind regards,\n{SIGNATURE}")),
    "S09_missing_fields": ("draft_request", "Invoice for USD 1,250.00 (PO-1003): invoice number and date needed", (
        "Hello Coastal Logistics team,\n\n"
        "We received a scanned invoice for USD 1,250.00 against PO-1003 for an LTL shipment from Chicago to "
        "Dallas. We cannot process it yet because two details are missing from the copy we received:\n\n"
        "1. The invoice number.\n"
        "2. The invoice date.\n\n"
        "Please upload a corrected copy that shows both through the secure link below; no login is needed. "
        "We will pick the invoice up again as soon as it arrives.\n\n"
        f"Kind regards,\n{SIGNATURE}")),
}


def _conf(value: Any, base: Decimal, bump: Decimal = Decimal(0)) -> float | None:
    if value is None or value == []:
        return None
    return float(min(Decimal("0.99"), base + bump))


def build_extraction(inv: InvoiceSpec, v: VendorInfo, buyer: dict[str, str], boxes: dict[str, Any],
                     page: int, *, scan: bool, number: str | None) -> dict[str, Any]:
    base = SCAN_CONF if scan else CLEAN_CONF
    rate = inv.tax_rate_pct
    data: dict[str, Any] = {
        "vendor_name": v.name,
        "vendor_address": v.address,
        "vendor_tax_id": v.tax_id,
        "vendor_email": v.email,
        "remit_bank": {"bank_name": v.bank_name, "account_last4": inv.bank_last4, "iban": None}
        if inv.bank_last4 else None,
        "invoice_number": number,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "payment_terms": inv.terms,
        "currency": "USD",
        "po_reference": inv.po_reference,
        "other_references": [r.rsplit(" ", 1)[-1] for r in inv.other_references],
        "bill_to": f"{buyer['name']}, {buyer['address']}",
        "lines": [
            {"description": ln.description, "qty": ln.qty, "unit_price": money(ln.unit_price),
             "amount": ln.amount, "tax_rate": rate, "uom": ln.uom}
            for ln in inv.lines
        ],
        "subtotal": inv.subtotal,
        "tax_total": inv.tax,
        "tax_inclusive": False,
        "shipping": None,
        "total": inv.total,
        "notes": inv.footer_note,
    }
    one = Decimal("0.01")
    data["confidence"] = {
        "vendor_name": _conf(v.name, base, one),
        "vendor_address": _conf(v.address, base, -one * 2),
        "vendor_tax_id": _conf(v.tax_id, base),
        "vendor_email": _conf(v.email, base, -one),
        "remit_bank": _conf(data["remit_bank"], base, -one * 2),
        "invoice_number": _conf(number, base),
        "invoice_date": _conf(data["invoice_date"], base),
        "due_date": _conf(data["due_date"], base),
        "payment_terms": _conf(inv.terms, base, -one),
        "currency": _conf("USD", base, -one * 3),
        "po_reference": _conf(inv.po_reference, base),
        "bill_to": _conf("x", base, -one),
        "lines": _conf(inv.lines, base, -one),
        "subtotal": _conf(inv.subtotal, base),
        "tax_total": _conf(inv.tax, base),
        "shipping": None,
        "total": _conf(inv.total, base, one),
    }
    loc_fields = ["vendor_name", "invoice_number", "invoice_date", "po_reference", "subtotal", "tax_total", "total"]
    data["location"] = {
        f: ({"page": page, "bbox": boxes[f]["bbox"]} if f in boxes and data.get(f) is not None else None)
        for f in loc_fields
    }
    return data


def _call(action: str, schema: str, output: Any, **when: Any) -> dict[str, Any]:
    return {"action": action, "schema": schema, "when": when, "output": output}


def build_llm_fixtures(sc: Scenarios, scenario: ScenarioSpec, boxes: list[dict[str, Any]]
                       ) -> dict[str, list[dict[str, Any]]]:
    """agent id -> calls (only agents that call the LLM for this scenario)."""
    sid, scan = scenario.id, scenario.render == "scan"
    invoices = sc.invoices(scenario)
    bundle = len(invoices) > 1
    buyer = sc.buyer.model_dump()
    number_override = scenario.override.get("invoice_number_printed")
    out: dict[str, list[dict[str, Any]]] = {}

    def add(agent: str, call: dict[str, Any]) -> None:
        out.setdefault(agent, []).append(call)

    # classifier: one call per uploaded file
    docs = [{"label": "invoice", "confidence": 0.9 if scan else 0.97, "page_from": i, "page_to": i,
             "reason": "Has an invoice number, line items and a total due." if (number_override or inv.invoice_number)
             else "Looks like a vendor invoice with line items and a total, but the invoice number is missing."}
            for i, inv in enumerate(invoices, start=1)]
    if bundle:
        for d in docs:
            d["reason"] = f"Page {d['page_from']} is a separate invoice from a different vendor."
    add("classifier", _call("classify", "ClassifierOutput", {"documents": docs}, document=scenario.file))
    reasons = {"tax_form": "A completed W-9 with the vendor's name, address and EIN.",
               "bank_letter": "A bank letter on letterhead confirming the account holder and account number."}
    for sf in scenario.supporting_files:
        add("classifier", _call("classify", "ClassifierOutput", {"documents": [
            {"label": sf.kind, "confidence": 0.96, "page_from": 1, "page_to": 1, "reason": reasons[sf.kind]}]},
            document=sf.file))

    for page, inv in enumerate(invoices, start=1):
        v = vendor_info(sc, scenario, inv)
        number = number_override or inv.invoice_number
        when_inv: dict[str, Any] = {"invoice_number": number} if bundle else {}

        ext = build_extraction(inv, v, buyer, boxes[page - 1], page, scan=scan, number=number)
        add("extraction", _call("extract", "InvoiceExtraction", ext,
                                **({"pages": [page, page]} if bundle else {})))

        # po_matching.match: explicit PO -> that PO; none -> every open PO of the vendor (if any)
        cands = [inv.po_reference] if inv.po_reference else [
            p.po_number for p in sc.purchase_orders if inv.vendor and p.vendor == inv.vendor]
        if cands:
            judged = []
            for po in cands:
                sim, pairs, reason = PO_JUDGEMENTS[(sid, po)]
                judged.append({"po_number": po, "line_similarity": sim, "reason": reason,
                               "pairs": [{"invoice_line_index": i, "po_line_no": p} for i, p in enumerate(pairs)]})
            add("po_matching", _call("match", "POInferenceOutput", {"candidates": judged}, **when_inv))
        if sid in GL_SUGGESTIONS:
            gl, dept, conf, why = GL_SUGGESTIONS[sid]
            add("po_matching", _call("suggest_gl", "GLCodingOutput", {"lines": [
                {"line_index": i, "gl_code": gl, "department": dept, "confidence": conf, "reason": why[i]}
                for i in range(len(inv.lines))]}, **when_inv))

        for key, (summary, nxt) in EXPLANATIONS[sid].items():
            inv_no, _, decision = key.rpartition("|")
            if inv_no and inv_no != number:
                continue
            add("decision", _call("decide", "DecisionExplanation", {"summary": summary, "next_step": nxt},
                                  **when_inv, decision=decision))

    if scenario.demo_remark and sid == "S02b_split_part2":
        add("reconciler", _call("apply_remark", "ChangeSet", {
            "ops": [
                {"op": "set_po_amount_note", "po_number": "PO-1001", "new_amount": Decimal("10500.00"),
                 "reason": "Procurement amended PO-1001 to $10,500 (reviewer remark)."},
                {"op": "set_gl", "line_index": 1, "gl_code": "6410", "department": None},
                {"op": "override_rule", "rule_id": "unmatched_lines",
                 "reason": "Reviewer accepted the Premium support add-on line."},
            ],
            "summary": "Raised PO-1001 to 10,500.00 for this invoice, accepted the Premium support line and "
                       "coded it to 6410 Software Support.",
        }, remark=scenario.demo_remark))

    if sid in COMMS:
        action, subject, body = COMMS[sid]
        add("comms", _call(action, "CommsDraft", {"subject": subject, "body": body}))
    return out


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() and o.as_tuple().exponent == 0 else float(o)
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def write_llm_fixtures(scenario: ScenarioSpec, fixtures: dict[str, list[dict[str, Any]]], llm_dir: Path) -> None:
    llm_dir.mkdir(parents=True, exist_ok=True)
    docs = [scenario.file, *[sf.file for sf in scenario.supporting_files]]
    for agent, calls in fixtures.items():
        payload = {"scenario": scenario.id, "agent": agent, "documents": docs, "calls": calls}
        text = json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n"
        (llm_dir / f"{scenario.id}__{agent}.json").write_text(text, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------------


def generate(scenarios_path: Path, data_dir: Path, llm_dir: Path, *,
             log: Callable[[str], None] = print) -> list[Path]:
    sc = load_scenarios(scenarios_path)
    written: list[Path] = []
    write_master(sc, data_dir / "master")
    written += sorted((data_dir / "master").glob("*.csv"))
    fx = data_dir / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    for scenario in sc.scenarios:
        number_override = scenario.override.get("invoice_number_printed")
        pdf, boxes = render_invoices(sc, scenario, number_override)
        if scenario.render == "scan":
            pdf = rasterise(pdf, seed=scenario.id)
        (fx / scenario.file).write_bytes(pdf)
        written.append(fx / scenario.file)
        for sf in scenario.supporting_files:
            if sf.kind == "tax_form":
                extra = render_w9(sc, scenario)
            elif sf.kind == "bank_letter":
                extra = render_bank_letter(sc, scenario, sf.account_holder or "", sf.bank_last4 or "")
            else:
                raise ValueError(f"{scenario.id}: unknown supporting file kind {sf.kind}")
            (fx / sf.file).write_bytes(extra)
            written.append(fx / sf.file)
        write_llm_fixtures(scenario, build_llm_fixtures(sc, scenario, boxes), llm_dir)
        log(f"  {scenario.id:<22} {scenario.file}  ({scenario.render}, {len(boxes)} page(s))")
    written += sorted(llm_dir.glob("*.json"))
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenarios", type=Path, default=REPO_ROOT / "data" / "scenarios.yaml")
    ap.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    ap.add_argument("--llm-dir", type=Path, default=BACKEND_DIR / "tests" / "llm_fixtures")
    args = ap.parse_args()
    print(f"Generating fixtures from {args.scenarios}")
    files = generate(args.scenarios, args.data_dir, args.llm_dir)
    print(f"Wrote {len(files)} files: master CSVs -> {args.data_dir / 'master'}, PDFs -> "
          f"{args.data_dir / 'fixtures'}, LLM fixtures -> {args.llm_dir}")


if __name__ == "__main__":
    main()

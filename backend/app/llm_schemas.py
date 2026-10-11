"""Pydantic models for every structured LLM output (SPEC section 7).

They are written for OpenAI strict JSON-schema mode: every property is required, unknown values are
null, no free-form dict keys. `llm.py` post-processes `model_json_schema()` (additionalProperties: false,
`required` = all properties, `oneOf` -> `anyOf` and no `discriminator` for ChangeSet.ops). Money is `Decimal` in Python but a plain JSON number in the schema.
FakeLLM fixtures in backend/tests/llm_fixtures/ validate against these models.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema

Amount = Annotated[Decimal, WithJsonSchema({"type": "number"})]
Score = Annotated[float, Field(ge=0, le=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- classifier.classify ---------------------------------------------------------------------------


class ClassifiedDocument(_Strict):
    label: str  # an id from document_types.yaml (llm.py injects the enum from config)
    confidence: Score
    page_from: int
    page_to: int
    reason: str


class ClassifierOutput(_Strict):
    documents: list[ClassifiedDocument]


# --- extraction.extract (InvoiceExtraction) -------------------------------------------------------


class RemitBank(_Strict):
    bank_name: str | None
    account_last4: str | None
    iban: str | None


class InvoiceLine(_Strict):
    description: str
    qty: Amount | None
    unit_price: Amount | None
    amount: Amount | None
    tax_rate: Amount | None  # percent, e.g. 8.25
    uom: str | None


class FieldConfidence(_Strict):
    vendor_name: Score | None
    vendor_address: Score | None
    vendor_tax_id: Score | None
    vendor_email: Score | None
    remit_bank: Score | None
    invoice_number: Score | None
    invoice_date: Score | None
    due_date: Score | None
    payment_terms: Score | None
    currency: Score | None
    po_reference: Score | None
    bill_to: Score | None
    lines: Score | None
    subtotal: Score | None
    tax_total: Score | None
    shipping: Score | None
    total: Score | None


class FieldLocation(_Strict):
    page: int  # 1-based
    bbox: list[float]  # [x0, y0, x1, y1] normalised 0..1 from the top-left


class FieldLocations(_Strict):
    vendor_name: FieldLocation | None
    invoice_number: FieldLocation | None
    invoice_date: FieldLocation | None
    po_reference: FieldLocation | None
    subtotal: FieldLocation | None
    tax_total: FieldLocation | None
    total: FieldLocation | None


class InvoiceExtraction(_Strict):
    vendor_name: str | None
    vendor_address: str | None
    vendor_tax_id: str | None
    vendor_email: str | None
    remit_bank: RemitBank | None
    invoice_number: str | None
    invoice_date: str | None  # ISO when unambiguous, else as printed
    due_date: str | None
    payment_terms: str | None
    currency: str | None
    po_reference: str | None
    other_references: list[str]
    bill_to: str | None
    lines: list[InvoiceLine]
    subtotal: Amount | None
    tax_total: Amount | None
    tax_inclusive: bool
    shipping: Amount | None
    total: Amount | None
    notes: str | None
    confidence: FieldConfidence
    location: FieldLocations


# --- po_matching.match / suggest_gl ---------------------------------------------------------------


class LinePair(_Strict):
    invoice_line_index: int  # 0-based
    po_line_no: int | None


class POCandidateJudgement(_Strict):
    po_number: str
    line_similarity: Score
    pairs: list[LinePair]
    reason: str


class POInferenceOutput(_Strict):
    candidates: list[POCandidateJudgement]


class GLLineSuggestion(_Strict):
    line_index: int
    gl_code: str
    department: str | None
    confidence: Score
    reason: str


class GLCodingOutput(_Strict):
    lines: list[GLLineSuggestion]


# --- decision.decide ------------------------------------------------------------------------------


class DecisionExplanation(_Strict):
    summary: str
    next_step: str


# --- reconciler.apply_remark (ChangeSet) ----------------------------------------------------------


class SetFieldOp(_Strict):
    op: Literal["set_field"]
    path: str  # e.g. "lines[2].amount"
    value: Amount | str | bool | None  # numbers parse as Decimal (money paths like lines[2].amount)


class SetGLOp(_Strict):
    op: Literal["set_gl"]
    line_index: int
    gl_code: str
    department: str | None


class LinkPOOp(_Strict):
    op: Literal["link_po"]
    po_number: str


class UnlinkPOOp(_Strict):
    op: Literal["unlink_po"]


class OverrideRuleOp(_Strict):
    op: Literal["override_rule"]
    rule_id: str
    reason: str


class SetPOAmountNoteOp(_Strict):
    op: Literal["set_po_amount_note"]
    po_number: str
    new_amount: Amount
    reason: str


class AddVendorMemoryOp(_Strict):
    op: Literal["add_vendor_memory"]
    hint: str


class AskOp(_Strict):
    op: Literal["ask"]
    question: str


ChangeOp = Annotated[
    SetFieldOp
    | SetGLOp
    | LinkPOOp
    | UnlinkPOOp
    | OverrideRuleOp
    | SetPOAmountNoteOp
    | AddVendorMemoryOp
    | AskOp,
    Field(discriminator="op"),
]


class ChangeSet(_Strict):
    ops: list[ChangeOp]
    summary: str


# --- comms.draft_request / draft_rejection --------------------------------------------------------


class CommsDraft(_Strict):
    subject: str
    body: str


SCHEMAS: dict[str, type[BaseModel]] = {
    m.__name__: m
    for m in (
        ClassifierOutput,
        InvoiceExtraction,
        POInferenceOutput,
        GLCodingOutput,
        DecisionExplanation,
        ChangeSet,
        CommsDraft,
    )
}

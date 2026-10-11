"""Decision (hybrid): the outcome is code, the explanation is LLM.

Code: decision = the highest severity among failed rules (rules.yaml decision_precedence), mapped to an
outcome by params.severity_outcomes. With no failures, auto-approve only if every auto_approve_gate passes.
Before reserving PO balance the po_balance rule is re-checked against the ledger as it is *now*, so two
split invoices decided back to back can't both pass on the same remaining amount.
Summary: the first sentence (decision + top reason) is built here from the rule results. The LLM (effort low)
only writes the detail after it, from the rule results. Detail that names a different decision
(params.decision_terms) is discarded, and so is a failed call: the rule explanations are used instead. So the
summary can never contradict the decision.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import delete

from app import po_ledger
from app.agents.base import Agent, AgentContext, AgentResult
from app.agents.po_matching import llm_context
from app.agents.policy import build_rule_context
from app.case_state import effective
from app.config_loader import RulesConfig
from app.llm import LLMError, sum_metrics, text_part
from app.llm_schemas import DecisionExplanation
from app.models import Case, RuleResult
from app.rules.engine import GateInput, dec, decide, evaluate_rule, fmt_money


def lead_sentence(label: str, results: list[dict[str, Any]], gate_failures: list[str], rules_cfg: RulesConfig) -> str:
    """Decision + its top reason, from the final rule results: the highest-precedence failed rule (rules.yaml
    order breaks ties), else the first failed auto-approve gate, else "every check passed"."""
    failed = [r for r in results if r["status"] == "fail"]
    if failed:
        rank = {sev: i for i, sev in enumerate(rules_cfg.decision_precedence)}
        top = min(failed, key=lambda r: rank.get(r["severity"], len(rank)))
        names = {r.id: r.fail_label or r.name for r in rules_cfg.rules}
        more = len(failed) - 1
        extra = f" (and {more} other failed check{'s' if more > 1 else ''})" if more else ""
        return f"{label}: {names.get(top['rule_id'], top['name'])}{extra}."
    if gate_failures:
        return f"{label}: {gate_failures[0]}"
    return f"{label}: every check passed."


def contradicts(text: str, outcome: str, terms: dict[str, list[str]]) -> str | None:
    """The first phrase in `text` that states a decision other than `outcome`, if any."""
    low = text.lower()
    return next((t for o, ts in terms.items() if o != outcome for t in ts if t.lower() in low), None)


def _drop_restated_decision(text: str, label: str) -> str:
    """'Rejected: this invoice...' after the lead 'Rejected: ...' -> 'This invoice...'."""
    text = text.strip()
    if text.lower().startswith(label.lower()):
        rest = text[len(label) :].lstrip(" :.,-")
        return rest[:1].upper() + rest[1:]
    return text


class DecisionAgent(Agent):
    async def run(self, action: str, ctx: AgentContext) -> AgentResult:
        p = ctx.params
        rules_cfg = ctx.config.rules
        state = effective(ctx.state)
        results: list[dict[str, Any]] = list(state.get("policy", {}).get("results") or [])
        inv: dict[str, Any] = state.get("normaliser", {}).get("invoice") or {}
        meta: dict[str, Any] = state.get("extraction", {}).get("field_meta") or {}
        pm: dict[str, Any] = state.get("po_matching", {})
        vendor = state.get("vendor", {}).get("vendor") or {}
        required = next((r.params.get("fields", []) for r in rules_cfg.rules if r.id == "required_fields"), [])

        gates = GateInput(
            required_confidences={f: (meta.get(f) or {}).get("confidence") for f in required},
            total=dec(inv.get("total")),
            match_type=pm.get("match_type") or "none",
            vendor_status=vendor.get("status"),
        )
        calc = decide(rules_cfg, results, gates)
        if calc.severity:
            outcome = p["severity_outcomes"][calc.severity]
        elif calc.gate_failures:
            outcome = p["gate_failure_outcome"]
        else:
            outcome = p["pass_outcome"]

        reasoning: list[str] = []
        if calc.severity:
            reasoning.append(
                f"Decision {outcome}: highest failed severity is '{calc.severity}' "
                f"({', '.join(calc.failed_rules)})."
            )
        elif calc.gate_failures:
            reasoning.append(f"Decision {outcome}: no rule failed, but an auto-approve gate did.")
            reasoning += calc.gate_failures[:2]
        else:
            reasoning.append(f"Decision {outcome}: every rule and auto-approve gate passed.")

        reservation: dict[str, Any] | None = None
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            po_id = pm.get("po_id")
            if outcome in p["reserve_po_balance_on"] and po_id:
                # no await between this re-check and the commit: the reservation is atomic in the event loop
                rc = build_rule_context(s, case, ctx.state)
                bal_rule = next((r for r in rules_cfg.rules if r.id == "po_balance" and r.enabled), None)
                now = evaluate_rule(bal_rule, rc) if bal_rule else None
                if (
                    now is not None
                    and now.status == "fail"
                    and "po_balance" not in calc.failed_rules
                    and outcome == p["pass_outcome"]
                ):
                    outcome = p["severity_outcomes"].get(bal_rule.severity, p["gate_failure_outcome"]) if bal_rule else outcome
                    calc.failed_rules.append("po_balance")
                    reasoning.append("PO balance changed since the policy check: " + now.explanation)
                    # keep the stored rule result and the explanation input consistent with the decision
                    results = [r for r in results if r["rule_id"] != "po_balance"] + [now.as_dict()]
                    s.execute(
                        delete(RuleResult).where(
                            RuleResult.case_id == case.id,
                            RuleResult.run_no == ctx.run_no,
                            RuleResult.rule_id == "po_balance",
                        )
                    )
                    s.add(
                        RuleResult(
                            case_id=case.id, run_no=ctx.run_no, rule_id=now.rule_id, status=now.status,
                            severity=now.severity, explanation=now.explanation, evidence=now.evidence,
                        )
                    )
                if outcome in p["reserve_po_balance_on"]:
                    lines = [
                        po_ledger.Reservation(ln.get("po_line_id"), amt, dec(ln.get("qty")))
                        for ln in pm.get("lines") or []
                        if (amt := dec(ln.get("amount"))) is not None
                    ]
                    # reserve the same figure po_balance checks (the subtotal): any gap goes on the header
                    gap = (dec(inv.get("subtotal")) or Decimal(0)) - sum((ln.amount for ln in lines), Decimal(0))
                    if gap > 0:
                        lines.append(po_ledger.Reservation(None, gap, None))
                    amount = po_ledger.reserve(s, case.id, po_id, lines)
                    po = rc.po
                    remaining = (rc.po_amount or Decimal(0)) - (po.committed_by_others if po else Decimal(0)) - amount
                    reservation = {"po_number": po.po_number if po else None, "amount": str(amount), "remaining_after": str(remaining)}
                    reasoning.append(
                        f"Reserved {fmt_money(amount)} on {reservation['po_number']}; "
                        f"{fmt_money(remaining)} left for other invoices."
                    )
            if reservation is None:
                released = po_ledger.release(s, case.id)
                if released:
                    reasoning.append(f"Released an earlier reservation of {fmt_money(released)}.")
            case.decision = outcome
            s.commit()

        label = p["decision_labels"].get(outcome, outcome)
        lead = lead_sentence(label, results, calc.gate_failures, rules_cfg)
        explanation, metrics, note = await self._explain(ctx, outcome, label, lead, inv, results, calc.gate_failures)
        if note:
            reasoning.append(note)
        with ctx.session_factory() as s:
            case = s.get(Case, ctx.case_id)
            assert case is not None
            case.decision_summary = " ".join(
                part for part in (lead, explanation["summary"], explanation["next_step"]) if part
            )
            s.commit()

        return AgentResult(
            outcome,
            {
                "decision": outcome,
                "severity": calc.severity,
                "failed_rules": calc.failed_rules,
                "gate_failures": calc.gate_failures,
                "reservation": reservation,
                "explanation": explanation,
            },
            reasoning[:5],
            confidence=1.0,
            metrics=metrics,
        )

    async def _explain(
        self,
        ctx: AgentContext,
        outcome: str,
        label: str,
        lead: str,
        inv: dict[str, Any],
        results: list[dict[str, Any]],
        gate_failures: list[str],
    ) -> tuple[dict[str, str], dict[str, Any], str | None]:
        """The detail after the code-built lead sentence: LLM text from the rule results only, or the rule
        explanations when the call fails or the text names another decision."""
        order = {"fail": 0, "warn": 1, "pass": 2}
        ranked = sorted(results, key=lambda r: order.get(r["status"], 3))
        payload = [
            {k: r[k] for k in ("name", "status", "severity", "explanation")} for r in ranked
        ] + [{"name": "Auto-approve gate", "status": "fail", "severity": "review", "explanation": g} for g in gate_failures]
        # the lead already carries the first gate failure
        from_rules = {
            "lead": lead,
            "summary": " ".join(([r["explanation"] for r in ranked if r["status"] == "fail"] + gate_failures[1:])[:2]),
            "next_step": "",
            "source": "rules",
        }
        with ctx.session_factory() as s:
            context = {**llm_context(ctx, s), "decision": outcome}
        try:
            out, call = await self.call_llm(
                ctx,
                "decide",
                DecisionExplanation,
                [text_part("Write the explanation now.")],
                prompt_vars={
                    "decision": outcome,
                    "lead": lead,
                    "vendor_name": inv.get("vendor_name") or "Unknown vendor",
                    "invoice_number": inv.get("invoice_number") or "(no number)",
                    "currency": inv.get("currency") or "",
                    "total": inv.get("total") or "(no total)",
                    "rule_results_json": json.dumps(payload, indent=1),
                    "max_sentences": ctx.params["explanation_max_sentences"],
                },
                context=context,
            )
        except (LLMError, RuntimeError) as e:
            return from_rules, {}, f"Explanation written from the rule results (LLM unavailable: {e})."
        assert isinstance(out, DecisionExplanation)
        detail = _drop_restated_decision(out.summary, label)
        clash = contradicts(f"{detail} {out.next_step}", outcome, ctx.params["decision_terms"])
        if clash:
            return from_rules, sum_metrics([call]), (
                f"The model's explanation said '{clash}', which is not the decision ({outcome}); "
                "used the rule explanations instead."
            )
        return {"lead": lead, "summary": detail, "next_step": out.next_step, "source": "llm"}, sum_metrics([call]), None

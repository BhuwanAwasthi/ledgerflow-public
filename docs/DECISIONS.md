# Decisions and assumptions log

Claude appends here when the spec is silent or a phase defers something. State these in the demo.

- PS-1 chosen; vendor onboarding (PS-2) is a light mini-flow inside one edge case.
- No ERP posting: the approved ERP import file (single or batch) stands in for posting.
- No outbound email API: vendor/submitter messages are drafts with mailto links; missing documents arrive via a no-login vendor portal link.
- Demo has no real auth: a role switcher (Submitter / AP Reviewer / Admin).
- 2-way match by default; GRN quantity check when a GRN exists.
- Tolerance: unit price must exceed both 2% and $50 per unit to fail; PO balance tolerance 2%.
- Implied PO matches never auto-approve.
- Seeded dashboard history is labelled as seeded demo data in the UI.

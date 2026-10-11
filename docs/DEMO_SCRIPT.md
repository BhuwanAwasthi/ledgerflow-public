# 5-minute demo script

Before recording: open the deployed URL 5 minutes early (it wakes the Render service), check that `/healthz` is green, set `features.llm_cache: false`, reseed, then pre-run **S02a** (so S02b can show the split) and **S03** (shown briefly at 4:20).

| Time | Screen | Say | Do |
| --- | --- | --- | --- |
| 0:00 | Dashboard | "AP teams match hundreds of PDF invoices to POs by hand. LedgerFlow turns each one into a decision with the reasoning visible, and keeps a human in charge of anything unusual." | Point at the KPIs and the straight-through % |
| 0:25 | Agent Studio → Workflow | "Eleven agents, all configured in YAML: stages, prompts, rules, ERP columns. The orchestrator is code; the model gathers evidence, and rules decide." | Show the graph and the price_tolerance rule |
| 0:45 | Submit → Live run | "Happy path: a clean Acme invoice." | Run S01; narrate stages as they land: classified, extracted, PO-1002 3/3 lines, all rules pass, **Auto-approve** |
| 1:40 | Approved library | "Approved invoices queue up here; download one, or export a batch as one Sage Intacct import file." | Download the S01 CSV and open it |
| 2:00 | Live run → Review | "Edge case 1: the second split invoice on PO-1001 goes over what is left of the PO and adds a line nobody ordered." | Run S02b → Needs review; open the review workspace: PDF left, template right, the po_balance and unmatched_lines reasons |
| 2:45 | Review | "The reviewer explains in plain words; the Reconciler turns that into exact changes and re-runs the checks." | Paste the demo remark; show v2 + diff + rules now passing; Approve |
| 3:25 | Live run → Vendors → Portal | "Edge case 2: a vendor we've never paid. Nothing is paid until they're verified, and no email API is needed: they get a secure upload link." | Run S05 → On hold; show the drafted message + link; open the portal, upload the W-9 + bank letter; Vendors shows verification checks; Approve vendor → case resumes |
| 4:20 | Review / Work queue | "Edge case 3, quickly: no PO number, only a quote. It proposes PO-1004 with a score, but never auto-approves a guess." | Show S03 (pre-run) candidates |
| 4:40 | Dashboard | "Every decision is reproducible: config snapshot, stage events, cost per invoice. Next steps would be ERP posting and email intake." | End |

Backup if the LLM is slow: Replay a finished run in the live view (it's labelled as a replay).

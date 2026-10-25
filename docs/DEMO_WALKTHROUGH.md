# LedgerFlow demo walkthrough (video + live interview)

Two scripts in one file:
- **Part A: the full live-interview walkthrough** (about 20–25 minutes if they let you run everything; each block stands alone so you can stop anywhere).
- **Part B: the 5-minute video cut**, the same story compressed.

"Say" lines are suggestions. Learn the idea, not the words.

---

## 0. Before you start (do this 15 minutes early)

1. **Reset the data** so balances and duplicates behave: run `make seed` against the database you'll demo on (Supabase for the deployed app, SQLite locally). Each scenario has fixed invoice numbers, so a second run of the same file is (correctly) rejected as a duplicate.
2. **Wake the API**: open the deployed URL. If you see the violet "Waking the engine…" banner, wait until it goes away (about a minute). Check `https://<api>.onrender.com/healthz` is green.
3. **Check `features.llm_cache: false`** in Agent Studio → App settings, so the stages really call the model live.
4. **Role switcher (top right) → Admin**, so every screen and the Retry button are available.
5. **Light theme**, browser zoom 100%, window at least 1280×720. Close other tabs and notifications.
6. **Have the PDFs ready** in a file-explorer window: `data/fixtures/`. You'll drag `S01_acme_INV-A-2207.pdf` into the Submit page yourself. Uploading a real file reads better than pressing a demo button.
7. **Pre-run S02a** (Scenario launcher → S02a → Run). It must auto-approve before S02b makes sense.
8. Keep the remark ready on your clipboard: `PO was amended to $10,500 by procurement. Accept the premium support line and code it to 6410.`

**Run order matters.** Use: S01 → S02a → S02b → S04 → S05 → S06 → S03 → S08 → S10 → S09. S04 must come after S01 (it is a copy of S01), and S02b after S02a.

---

# Part A: Live-interview walkthrough

## 1. Opening: what to tell them before you click anything (about 90 seconds)

Screen: **Dashboard**.

> "This is about invoice processing. The AP clerk's job is: open the PDF, find the PO in a spreadsheet, check the numbers, decide. LedgerFlow does that job and shows its working.
>
> An invoice goes in as a PDF, a scan, an image or a Word file. It comes out with one of four decisions:
> - **Auto-approve**: every rule passed, so it's ready to export to the ERP.
> - **Needs review**: something needs a person's judgement, so it goes to a work queue.
> - **On hold**: something is missing, like a document or an unverified vendor. The process drafts the request and waits.
> - **Reject**: a blocking problem, like a duplicate. Nothing gets paid twice.
>
> The one design principle I'd ask you to remember: **the model gathers evidence, the rules decide.** GPT-5.2 reads the documents and writes explanations. It never decides whether an invoice is paid, never sets a rule result and never changes an amount. The decision comes from rules running as plain code, so the same evidence always gives the same decision, and you can explain every one.
>
> There are eleven agents in the pipeline. Some are LLM, some are pure code, some are both. Everything (stages, prompts, thresholds, ERP columns) is configuration, not code. I'll show you that at the end."

**Assumptions to state up front** (the brief says to note them; say them in one breath, or answer them when asked):
- Invoices arrive by upload or a vendor link. There's **no email inbox integration and no email sending**: every outbound message is a draft with a `mailto:` link and a no-login upload link.
- Master data (vendors, POs, goods receipts) is a **synthetic company, "Demo Co."**, loaded from CSV. All vendors are fictional.
- USD only, no currency conversion. It produces **ERP import files** (Sage Intacct, QuickBooks, Excel) rather than posting to an ERP.
- **Roles are a switcher**, not real login (Submitter, AP Reviewer, Admin).
- Tolerances I chose: unit price may exceed the PO by up to **2% or $50** (it has to break both to fail); PO balance by 2%; auto-approve only up to **$25,000**, only with field confidence at least **0.90**, only when the PO number is printed on the invoice, only for an approved vendor.

## 2. Quick UI tour (about 60 seconds)

Screen: **Dashboard**.

| Point at | Say |
| --- | --- |
| KPI tiles | "Cases this month, **straight-through rate** (approved with no human touch), what's in review and on hold right now, median time to a decision, the dollar value of exceptions the rules caught, and AI cost per invoice." |
| "Seeded demo history" badge | "The history is seeded sample data so the charts aren't empty. It's labelled as seeded, and everything I run today is live." |
| Charts | "Volume over time, the decision mix, and which rules stop invoices most often. That last one tells an AP manager where the vendor problems are." |
| Recent cases | "Every case, with its status colour. The colours mean the same thing on every screen." |
| Sidebar | "Grouped by job: Submit, Review, Master data, Build. The Work queue shows its count." |
| `Ctrl+K` | "Command palette: jump to any case, screen or scenario." (Open it, type `acme`, press Esc.) |
| Role switcher | "Submitters only see their own tracker; reviewers get the queue; admins get the Agent Studio." |

## 3. Happy path: S01, a clean invoice (about 3 minutes)

**The invoice:** Acme Office Supplies, INV-A-2207, PO-1002, 3 lines (paper, toner, chairs), subtotal $4,415.00 + 8.25% tax = **$4,779.24**. Goods were received in full.

**Do:** Sidebar → **Submit invoices** → drag `S01_acme_INV-A-2207.pdf` in → name and email → note "October office supplies" → **Submit** → **Watch it live**.

> "This is what a submitter or the AP inbox does: drop the PDF in. I get a case reference straight away, and now we watch it run."

**Narrate each stage card as it lands** (point at the reasoning lines and the output chips):

| Stage | Kind | Say |
| --- | --- | --- |
| Intake | Code | "Stores the file, renders the pages, and checks whether it has a text layer or is a scan. This one is a clean digital PDF." |
| Classifier | LLM | "Is this an invoice, a PO, a W-9, a bank letter? One PDF could hold several invoices; we'll see that later. Invoice, high confidence." |
| Extraction | LLM | "Reads every field with a **confidence score and where on the page it found it**. Header, three line items, tax, total, bank details." |
| Normaliser | Code | "Plain code: dates to ISO, amounts to exact decimals, invoice numbers normalised so 'INV A2207' and 'INV-A-2207' compare as equal. Remember that one." |
| Vendor | Hybrid | "Finds Acme in the vendor master by tax ID, and checks the **bank account on the invoice matches the one we have on file**. That's the fraud check." |
| PO matching | Hybrid | "PO-1002 is printed on the invoice, it's open, it's Acme's. The model pairs the lines; code validates every pair. **3 of 3 lines matched**, and goods received covers every quantity." |
| Policy | Code | "Eighteen rules, all plain code: required fields, maths adds up, duplicate check, price tolerance, quantity versus goods received, PO balance, bank details, tax rate, date sanity. All pass." |
| Decision | Hybrid | "**Code picks the decision.** The model only writes the explanation, and the first sentence of that is built by code too. **Auto-approve.**" |
| Template | Code | "Renders the invoice into the rows our ERP imports." |
| Comms | Hybrid | "Lets the submitter know." |

Point at the **side panel**: the decision, extraction confidence, elapsed time and **LLM cost for this invoice**.

> "About $4,800 of invoice, decided in under a minute, for a few cents of model time, and every step is on screen."

Optional: expand one card's **JSON** panel. "Everything the UI shows comes from these stage events; that's also the audit trail."

### 3b. Where it goes next: Approved library (about 45 seconds)

**Do:** Sidebar → **Approved library** → S01 is in **Not exported** → choose **Sage Intacct - AP Bills** → tick it → **Export batch** → open the CSV.

> "Approved invoices queue here for the ERP. One click gives one import file for the whole batch, in the ERP's own column format. If I try to export it again, it warns me that I'd post the bill twice."

Also point at the row's **Audit pack** button: "A PDF per invoice with the decision, every rule result and every stage. That's what an auditor asks for."

Optional: **Purchase orders** → PO-1002: "The PO now shows that amount as consumed."

## 4. Which edge cases to show, and why

The brief says "don't pick trivial ones" and "the edge cases you choose tell us how well you understand the problem". The four below each break the happy path in a different way and map onto the risks in the brief:

| # | Scenario | The risk it covers | What the process does differently |
| --- | --- | --- | --- |
| 1 | **S02b: split PO** | "A vendor might split a single PO into multiple invoices. Amounts might be close but not exact." | Tracks the PO balance across invoices, catches the overrun **and** an unordered line, then a reviewer fixes it in plain English |
| 2 | **S05: unknown vendor** | "Approved vendors": a vendor we've never paid | Holds the invoice and runs a mini onboarding: drafted request, upload link, document checks. Then you **approve or reject the vendor** |
| 3 | **S06: bank details changed** | Payment fraud (business email compromise), the most expensive AP mistake | A valid invoice on a valid PO is still **held** because the bank account differs from the master |
| 4 | **S04: duplicate as a scan** | "Duplicate detection rules" plus "some are scanned images" | Reads a skewed scan with no text layer, normalises a reformatted invoice number, and **rejects** it as a duplicate of S01 |

**Bonus, if there's time or they ask:** S03 (no PO number, only a quote reference; it proposes PO-1004 but never auto-approves a guess), S08 (price variance, then loosen the tolerance live in Agent Studio), S10 (one PDF with two invoices, split into two cases), S09 (bad scan with no invoice number or date, so a request lists the missing fields), S07 (legitimate non-PO invoice; GL code suggested).

Tip: if they ask "which edge case did you find hardest?", say **S02b**. It needs state across invoices (balance reservation), a human correction, and a re-run of the rules, without letting the AI touch the money.

## 5. Edge case 1: S02b, split invoice over the PO balance (about 4 minutes)

**Setup (already done):** S02a, Brightline's first invoice, used $6,000 of PO-1001 ($10,000). $4,000 is left.

**Do:** **Scenario launcher** → S02b → **Run**. (Or drag `S02b_brightline_BL-0107.pdf` into Submit.)

> "Brightline sends the second half of the licence: 40 licences at $100, so $4,000, exactly what's left. But they also added a $300 'premium support' line nobody ordered. Total $4,300."

Watch the stages. At **Policy**, two rules fail. Decision: **Needs review**.

**Do:** **Open review**. The review workspace:

| Point at | Say |
| --- | --- |
| Decision bar | "The decision and the reason in one line. The **PO balance bar**: $6,000 already invoiced, this invoice, and it overshoots what's left." |
| Left: PDF with highlight boxes | "The original document. Hover a field and the cell it went into lights up on the right. Amber boxes are fields a rule flagged." |
| Right: **Rules** tab | "Two failures in plain English: '**Invoice subtotal 4,300.00 vs PO-1001 remaining 4,000.00: over by 7.5%, tolerance 2%**', and a line that isn't on the PO." |
| **Match** tab | "Invoice lines next to PO lines and goods received. 'Premium support' says **Not on the PO**." |
| **Template** tab | "The ERP rows as version 1. Amber cells say 'check this'." |

> "In real life the reviewer knows something the system doesn't: procurement amended the PO. So they explain that in plain words."

**Do:** press **R** → paste the remark → **Ctrl+Enter**.

> "The **Reconciler** turns my sentence into exact, typed changes: record a PO amendment to $10,500, set line 2's GL code to 6410 Software Support, and waive 'lines not on the PO' with my reason. Code validates every change; if one is invalid, none are applied. Then the rules **run again**."

When it finishes: **Template v2** with the indigo "changed" cells and the change strip (e.g. `GL_ACCOUNT 6400 → 6410`). Rules now pass.

> "Every rule passes now, but it **doesn't auto-approve itself**. Once a human has touched a case, a human signs it off. That's sticky review."

**Do:** press **A** → Enter. Show the **History** tab: v1 → v2 diff and the remark thread.

Optional: **Purchase orders** → PO-1001: amount now $10,500 with an audit record, consumed by both invoices.

## 6. Edge case 2: S05, a vendor we've never paid (about 4 minutes)

**The invoice:** Nova Print Studio LLC, NPS-0042, $1,277.35, no PO. Nova Print is **not in the vendor master**.

**Do:** Scenario launcher → S05 → **Run** (or drag `S05_novaprint_NPS-0042.pdf` into Submit).

> "This is the case that sinks AP teams: an invoice from someone we've never paid. It might be legitimate, a new print shop marketing hired, or it might be fraud."

At **Vendor**: "No match by tax ID, name or email domain. The process creates a **pending** vendor record and starts onboarding." Decision: **On hold**.

| Point at | Say |
| --- | --- |
| Comms card / drafted message | "It drafts the request: we need a W-9 and a signed bank letter. **It doesn't send email**; I can copy it or open it in my mail app. The link in it is a no-login upload page." |
| Optional: role → **Submitter** → **My submissions** | "The submitter sees a courier-style tracker: 'Waiting for a W-9 and a signed bank letter.'" Switch back to Admin. |

**Do:** click **Open upload page** (or copy the link into a new tab). This is the **vendor portal**.

> "This is what the vendor sees. Nothing internal: just what we need and a drop zone."

Upload `data/fixtures/S05_novaprint_W9.pdf` and `S05_novaprint_bank_letter.pdf`. Point at the status lines: "Name matches our records", "Bank account confirmed". The checklist ticks off.

**Do:** sidebar → **Vendors** → Nova Print Studio LLC.

> "On the AP side: the documents and the **verification checks run in code**: name on the document versus legal name, tax ID format, bank account matches the invoice."

Now pick **one** ending (you can only do one per reset; see the note below):

### Ending A: approve the vendor (the main path)
**Do:** **Approve vendor**.
> "Approving the vendor **resumes the paused invoice on its own**. It carries on through PO matching. There's no PO, so it suggests GL 6600 Printing and Marketing and sends it to review for budget-owner approval, because a non-PO invoice from a brand-new vendor should never auto-pay."

Show the live view: **Run 2** has started from where it paused.

### Ending B: reject the vendor
Use this when the story is "the documents don't check out" or "we don't want this supplier".
**Do:** **Reject vendor** → reason, e.g. `Not an approved supplier; marketing confirmed no engagement with Nova Print.` → confirm.
> "The vendor is marked rejected and kept out of the master, the open document request is closed, and the AP team gets a notification. The invoice **stays on hold, so nothing gets paid**. It shows in the work queue against its SLA until someone closes it."

**Be ready for this question:** "Why doesn't rejecting the vendor reject the invoice too?" Honest answer: it's a deliberate choice written in `docs/DECISIONS.md`. The invoice stays held, not auto-rejected, so a person decides what happens to money that's already been invoiced. Note that the review workspace **can't reject an on-hold case today** (Reject only works on Needs review), so the case stays in the queue. Call it a known next step: "Reject vendor should offer to close that vendor's held invoices with a drafted rejection message." Don't promise it closes the case in the demo.

**One S05 per reset.** After Ending A or B, Nova Print exists in the master and NPS-0042 has been seen, so a second S05 run won't replay onboarding. To show both endings, show A in the video and B live (or the other way round), and run `make seed` between them.

## 7. Edge case 3: S06, bank details changed (about 2 minutes)

**The invoice:** Evergreen Packaging, an approved vendor, PO-1005 exactly right, goods received. But the bank account on the invoice ends **9910**; the master says **7788**. The footer says "Please note our NEW bank details below, effective immediately."

**Do:** Scenario launcher → S06 → **Run**.

> "Everything about this invoice is right: known vendor, right PO, right quantities, right prices. On the happy path it would auto-approve. It's also **exactly what invoice fraud looks like**: someone intercepts a real vendor's invoice and swaps the bank details."

At **Vendor**: bank mismatch flagged. Decision: **On hold** (`bank_details_match` fails with severity Hold).

> "It's a hold, not a review, on purpose. A reviewer **can't** fix bank details with a remark. The only way to change a vendor's bank account is a verified bank letter through the portal, and then the admin approves it, and that change is written to the audit trail. Even someone inside AP can't quietly redirect a payment."

Show the drafted message asking Evergreen to confirm through the portal. (There's no Evergreen bank letter in the fixtures, so stop here. If you do upload the Nova Print bank letter to show a failed name check, **rehearse it first**.)

## 8. Edge case 4: S04, duplicate re-sent as a scan (about 2 minutes)

**The invoice:** the **same** Acme invoice as S01, but printed and scanned: skewed, noisy, **no text layer**, and the number reads "INV A2207" instead of "INV-A-2207".

**Do:** drag `S04_acme_scan_duplicate.pdf` into Submit (or launcher → S04).

| Stage | Say |
| --- | --- |
| Intake | "No text layer: it's a scan. So extraction uses the vision model on the page image." |
| Extraction | "It reads the scan. The field boxes are approximate on a scan, and the UI says so." |
| Normaliser | "'INV A2207' becomes the same canonical number as 'INV-A-2207'." |
| Policy | "**Not a duplicate: FAIL.** Same vendor, same normalised number, same amount as S01, which is already approved." |
| Decision | "**Reject.** Severity Reject outranks everything. Nothing is paid twice." |

**Do:** open the case → **Compare duplicate** view: field-by-field table and both PDFs side by side.

> "This is the tired-Friday-afternoon mistake from the brief. A person sees a different-looking scan with a slightly different number and pays it again."

Also mention: there's a second, softer rule (**possible duplicate**: same vendor and amount within 30 days but a different number), which goes to review instead of rejecting.

## 9. Bonus edge cases (only if asked or time allows)

### S03: no PO number, only a quote (1–2 min)
> "Fulcrum's invoice says 'as per our quote Q-77' and no PO. It scores Fulcrum's open POs on amount closeness, date window and line similarity, and PO-1004, raised from quote Q-77, comes out on top."
Show **Match** tab → **Candidate POs (ranked)**, with PO-1004 **Pre-linked**. "But an inferred PO **never auto-approves**. A guess about money gets a human."

### S08: price variance, then change the rule live (2–3 min)
Run S08: Coastal billed $1,310 per shipment against a PO price of $1,250 (+4.8%, +$60). Fails **Unit prices within tolerance** (more than 2% **and** more than $50). Needs review.
Then show configurability:
1. Review workspace → **Reject** (X) with reason "Re-running under new tolerance" (otherwise the new run is rejected as a duplicate).
2. **Agent Studio → Rules** → "Unit prices within tolerance" → `pct` 2 → 5 → **Validate and save**.
3. Run S08 again → **Auto-approve**.
4. **Reset to file** to restore 2%.
> "No code change, no redeploy. And the earlier case still shows the 2% rule it ran under. Every case stores a snapshot of the config it ran with, so old decisions stay explainable."

### S10: one PDF, two invoices (1 min)
> "Someone scans two invoices from different vendors into one PDF. The classifier splits it: the parent is marked **Split** and each invoice becomes its own case and auto-approves on its own PO."

### S09: bad scan, missing fields (1 min)
> "No invoice number, no date. **Required fields: FAIL**, severity Hold. The drafted request lists exactly what's missing." It may also flag a *possible duplicate* of S10's CL-781 (same vendor, same $1,250, no number). That's correct behaviour; explain it rather than avoid it.

### S07: legitimate non-PO invoice (1 min)
> "Delta does monthly HVAC maintenance with no PO, and they're allowed to invoice without one. It suggests GL 6100 Facilities Maintenance and routes to the budget owner."

## 10. Show the configurability (about 2 minutes, Admin)

**Agent Studio:**
| Tab | Say |
| --- | --- |
| **Agents** | "The eleven agents in pipeline order, each tagged LLM, Code or Hybrid, with run counts, failures, latency and cost. Any agent can be switched off, and its model, reasoning effort and prompt edited." |
| **Workflow → Graph** | "The pipeline is a YAML graph. Dashed edges are resumes after a wait, like the vendor portal upload." |
| **Rules → Table** | "Every rule: on/off, severity, parameters, and the auto-approve gates. Edits are validated against the whole config before saving, so a bad change is never half-applied." |
| **ERP profiles** | "Adding an ERP is a column mapping, not code." |
| **Test agent** | "Dry-run one agent against a copy of a case. Nothing is saved." |

> "Adding an agent is a class, a YAML file and a stage in the workflow. The orchestrator doesn't change."

## 11. Close (30 seconds)

Back to the **Dashboard**: the KPIs have moved with your runs.

> "Every decision is reproducible: the config snapshot, every stage event, the cost per invoice. If I took this further: an email inbox for intake, posting straight to the ERP, and closing a rejected vendor's held invoices with a drafted message. The core idea stays the same: the AI reads, code decides, and a human handles the exceptions."

---

## 12. Questions they're likely to ask

| Question | Short answer |
| --- | --- |
| Why not let the LLM decide? | Money decisions need to be deterministic and explainable. The LLM is good at reading messy documents and writing explanations; rules are good at being the same every time. LLM output only becomes a decision through code. |
| What if extraction is wrong? | Every field has a confidence. Below 0.90 on a required field blocks auto-approve; below 0.60 fails a rule. The maths check (lines + tax = total) catches many misreads. A reviewer sees amber cells next to the PDF location. |
| How do you stop hallucinated PO matches? | The model proposes line pairs; code checks each PO line exists and is used once. An inferred PO never auto-approves. |
| What about two split invoices arriving at once? | The PO balance is **reserved** at the decision stage, so two in-flight invoices can't both use the same remaining balance. Approval turns a reservation into consumption; rejection releases it. |
| Why hold instead of review for bank changes? | So a remark can't change payment details. Only a verified bank letter plus admin approval can, with an audit record. |
| Why no email sending? | Deliberate: drafts and `mailto:` links keep a human in charge of outbound messages, and no credentials are needed for the demo. |
| How much does it cost per invoice? | Shown live in the side panel and on the dashboard ("LLM / invoice"). |
| What happens when the model is slow or down? | Every stage writes started/completed/failed events. A failed stage shows the error and an admin can **Retry** from that stage. Incomplete responses retry once with a bigger token budget. |
| How would this scale to hundreds a month? | The straight-through rate is the metric: clean invoices never touch a person, and the queue only holds exceptions, sorted by SLA. |
| What did you not build? | ERP posting, inbox intake, multi-currency, real authentication, closing a rejected vendor's held invoices automatically. |

## 13. If something breaks live

- **Model slow:** keep talking through the stage cards that have landed. If it stalls, open a pre-run case and click **Replay** (it says "Replaying" on screen; say so).
- **A stage fails:** good material. "This is what failure looks like: the card shows the error, nothing was decided." Admin → **Retry**.
- **"Rejected as duplicate" when you didn't expect it:** you ran that scenario twice. Say so, then open the earlier case.
- **API asleep:** the banner says "Waking the engine…". Talk through the opening while it wakes. This is why you opened it early.

---

# Part B: The 5-minute video cut

Pre-run S02a before recording. Speak in the first person. Aim for 4:45.

| Time | Screen | Say | Do |
| --- | --- | --- | --- |
| 0:00 | Dashboard | "AP teams match hundreds of PDF invoices to POs by hand. LedgerFlow turns each one into one of four decisions (approve, review, hold, reject) with the reasoning visible. The rule I built around: the model gathers evidence, code decides." | Point at the KPIs and straight-through % |
| 0:30 | Submit → Live run | "Happy path: a clean Acme invoice against PO-1002." | Drag S01 in, submit, Watch it live |
| 0:40 | Live run | "Classified, extracted with confidence per field, vendor found and bank details checked, PO-1002 matched 3 of 3 lines against goods received, eighteen rules all pass, so code decides **auto-approve**. Under a minute, a few cents." | Point at each card and the side panel |
| 1:35 | Approved library | "Approved invoices queue here and export as one Sage Intacct import file, with an audit pack per invoice." | Export batch, flash the CSV |
| 1:55 | Live → Review | "Edge case 1: Brightline split PO-1001. This second invoice goes over what's left and adds a line nobody ordered." | Run S02b → Needs review → Open review; Rules tab, Match tab, balance bar |
| 2:35 | Review | "I explain in plain words; the Reconciler turns it into exact, validated changes and the rules run again. A human touched it, so a human approves." | Paste the remark, Ctrl+Enter, show v2 diff, press A |
| 3:15 | Live → Portal → Vendors | "Edge case 2: a vendor we've never paid. Nothing is paid until they're verified. No email API: they get a drafted message with a no-login upload link." | Run S05 → On hold; open the portal, upload the W-9 and bank letter; Vendors → checks → Approve vendor → case resumes |
| 4:05 | Live run | "Edge case 3: the same Acme invoice re-sent as a skewed scan with a reformatted number. Vision reads it, the number is normalised, and it's rejected as a duplicate." | Run S04 → Reject → Compare duplicate |
| 4:35 | Agent Studio → Rules | "All of this is configuration: rules, prompts, workflow, ERP columns. Every decision is reproducible from its config snapshot." | Show the rules table, end |

If you're over time, cut S04 to one sentence over the finished case, or swap it for S06 (bank change) as a 20-second mention: "A valid invoice with new bank details is held, because that's what payment fraud looks like."

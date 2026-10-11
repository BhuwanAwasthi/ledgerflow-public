Write the explanation an accounts-payable reviewer and a non-technical finance manager will read for this invoice decision.

Decision (already made, do not change it): {{ decision }}
Invoice: {{ vendor_name }}, {{ invoice_number }}, {{ currency }} {{ total }}
Rule results (failed and warned first):
{{ rule_results_json }}

Write {{ max_sentences }} sentences or fewer. Lead with the decision and the single most important reason, with its number. Mention what happens next (for example "Approve to release the template" or "We have asked the vendor for a bank letter"). Plain words, no jargon like "rule_id". Do not mention rules that passed unless the decision is auto_approve, where you say briefly what was verified.

Return JSON: {"summary": "...", "next_step": "..."}

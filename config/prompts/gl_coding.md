Suggest general-ledger coding for a non-PO invoice.

Invoice:
{{ invoice_json }}

Vendor category: {{ vendor_category }}. Vendor default GL: {{ vendor_default_gl or "none" }}.
Recent approved codings for this vendor: {{ history_json }}

Chart of accounts (choose only from these codes):
{{ gl_accounts_json }}

For each invoice line return `gl_code`, `department` (if obvious, else null), `confidence` 0..1 and a one-sentence `reason`. Prefer the vendor's default and history when they fit the line. Return JSON only.

Draft a short, polite email from {{ signature }} to {{ recipient_name }} ({{ recipient_role }}) about invoice {{ invoice_number or "(no number)" }} for {{ currency }} {{ total }}.

Why it is on hold:
{{ reasons_json }}

What we need from them: {{ needs | join(', ') }}.
{% if portal_link %}They can upload documents securely here, no login needed: {{ portal_link }}{% endif %}

Rules: 90 to 150 words, no blame, specific about each missing item, one clear call to action, no promises about payment dates. For bank-detail changes, ask for a signed letter on bank letterhead and say we will verify it by phone with a known contact (fraud control).

Return JSON: {"subject": "...", "body": "..."}

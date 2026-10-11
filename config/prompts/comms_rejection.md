Draft a short, neutral email from {{ signature }} to {{ recipient_name }} explaining that invoice {{ invoice_number }} for {{ currency }} {{ total }} was not accepted.

Reason(s):
{{ reasons_json }}

If it is a duplicate, name the original reference and date and say no action is needed if it was sent twice by mistake. 60 to 120 words. Return JSON: {"subject": "...", "body": "..."}

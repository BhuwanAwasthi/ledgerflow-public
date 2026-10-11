An invoice has no explicit PO number. Decide which, if any, of the vendor's open purchase orders it most likely bills against.

Invoice:
{{ invoice_json }}

Candidate open POs for this vendor (with lines and remaining balance):
{{ candidates_json }}

For each candidate return `line_similarity` (0 to 1): how well the invoice line descriptions and quantities correspond to the PO lines (ignore price differences; they are checked elsewhere). Also return `pairs`: for each invoice line index, the best matching PO line number or null, and a one-sentence `reason` per candidate a reviewer can read.

Do not pick a winner; the system scores candidates itself. Return JSON only.

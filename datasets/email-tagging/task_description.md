# Email Tagging (Pilot) — Routing-Category Classification

Read one inbound email and label it with exactly one routing category. This is a pilot mirror of the
live inbox classifier: the tag decides where the email goes next.

## Domain

- Input: one email (From / Subject / Direction + a short body, sometimes an attachment line)
- Output: exactly one category label — one of `financial`, `actionable`, `informational`, `other`
- Categories (starting gists — intentionally thin):
  - `financial` — about an invoice, payment, or charge
  - `actionable` — the recipient needs to do something
  - `informational` — just letting the recipient know something
  - `other` — none of the above

## Success criteria

- Exact Match: the output, lowercased and whitespace-stripped, equals the gold label exactly
- The model must emit *only* the label — no explanation, no punctuation

## Key failure modes (the hard part)

- Emails that *look* like one category but belong to another — a routine "renewed / shipped / receipt"
  note that still involves a charge or refund; a calendar invite or a request buried in a friendly message.
- Adding any explanation around the label (the grader compares the whole output).
- Inventing a label outside the four allowed values.

## Notes

- Small, deliberately hard set (15 rows) — every row sits on a category boundary, so the starting prompt
  misses several and the optimizer has room to sharpen the routing rules (e.g. how to treat money, how to
  tell a request apart from a notification).
- The starting prompt is a floor: it names the four categories but gives no boundary rules, leaving the
  optimizer room to discover them.

# Demo — Support-Ticket Classification

Read a short customer-support ticket and label it with exactly one of five categories. This is the try-and-learn demo: a small, relatable task that shows a full optimize loop end-to-end in a few rounds.

## Domain

- Input: one customer-support message (a sentence or two)
- Output: exactly one category label — one of `refund`, `password_reset`, `bug_report`, `shipping_status`, `billing_inquiry`
- Challenge: tickets are phrased in everyday language; some are ambiguous (e.g. "I was charged twice" sits between `billing_inquiry` and `refund`)

## Success criteria

- Exact Match: the model's output, lowercased and whitespace-stripped, equals the gold label exactly
- The model must emit *only* the label — any extra explanation breaks the match

## Key failure modes

- Adding explanation or punctuation around the label (the grader compares the whole output)
- Inventing a label outside the five allowed values
- Confusing adjacent intents: refund vs billing_inquiry, bug_report vs shipping_status

## Notes

- A small, balanced set (3 examples per category) — enough signal to see the prompt improve without a long run.
- The starting prompt is a deliberate floor: it names the categories but gives little disambiguation guidance, leaving the optimizer room to sharpen the category definitions and the answer format.

# Email Tagging (Pilot)

Pilot dataset for refining the inbox **email-classification** prompt — the `subject-classifier-LM`
routing decision in the `04_inbox-attachment-organizer` n8n microservice. Classifies an inbound email
into one of four routing categories: `financial`, `actionable`, `informational`, `other`.

## Type

Single `llm_only` generation node, no retrieval. Reads an email and emits one category label against a
running TermNorm backend.

## Data — provenance & anonymization

- 15 rows, **fully anonymized**. Every name, email address, company, amount, and reference is fictional.
- Grounded in the *topic distribution* of the real `PCRM_root_TestObject - Entries.tsv` (the CRM contacts
  export), but reconstructed: the TSV holds CRM **output** (contacts), not source emails or
  `type_of_document` labels. Each sample is a short email rebuilt from a realistic topic, then labeled.
- **Hard samples only** — every row sits on a category boundary (a charge dressed as a routine notice, a
  request hidden in a friendly note, a marketing invite that tempts `actionable`). The whole set is the
  train split.
- **Provisional gold.** Labels are assigned from the email content per the classifier's intended
  category rules — pilot signal, not a curated benchmark. Curating real emails + gold is the follow-up.

## Scoring

`exact_match(predicted, ground_truth)`. The answer-format contract is the live string
`matchers.py::EXTRACTION_NOTES`, already fed to the prompt by the origin resolver — read it there,
not from a paraphrase.

## Follow-ups (not in this pilot)

- Real source emails + curated gold replace the reconstruction step.
- `extract_depth` (1-3) as a second tagged axis.
- CRM-entry free-text generation (`last_topic` / `notes`) — needs a semantic / LLM-judge or field-F1
  scorer, not exact-match.

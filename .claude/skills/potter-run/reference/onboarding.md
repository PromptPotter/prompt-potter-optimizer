# Onboarding a dataset that has never run

Read this only in **onboarding mode** — a dataset with no campaign, no loader, or a cold
machine. If a campaign already exists, this file is not the one you want.

## The flagship flow — upload → context → origin → select · modify · start

The default for a real client/tenant task (not a bundled benchmark): one chat-shaped flow,
one origin, no hand-written loader. A registered benchmark skips all of it and uses
`new <name>`.

**Fully local.** The user clones the repo, starts TermNorm (`:8000`) and PromptPotter
(`:8001`), drops their dataset, and runs end to end. Nothing leaves the machine except what
those two servers do internally.

**Web flow:**

1. Sidebar → "Start a new campaign" opens the IngestPane.
2. Upload CSV / TSV / JSON / JSONL / XLSX, ≤25 MB, ≤500 rows → a server-held `DraftCampaign`
   (nothing on disk yet).
3. The user types what the prompt is supposed to do. Submitting marks it CONFIRMED.
4. One `checkin` turn proposes the column map (`query` + `ground_truth`), the six decomposed
   Layer-1 prompt fields, and the 7-field `task_context`; code fills the closed-label answer
   space deterministically. A pure checklist gate (no LLM) blocks mint until query +
   ground_truth + framing are all CONFIRMED and every active LLM node owns a model —
   no individual prompt field is gated (the optimizer evolves them).
5. The origin lands as **round 0 / C0** in the lineage tree.
6. Select · modify · start. Mint writes the tenant dataset + campaign + cycle and runs from
   round 0.

**CLI parity:**

```bash
python -m promptpotter new <file.csv> --set task_description='what the prompt does'
```

Same chain, same seam: `ingest_draft` → `resolve_origin_turn` → `prepare_checkin_run`. Omit
`--set` to let the resolver propose the framing and ask.

Seam: `application/datasets/` (`ingest.py`, `origin_resolve.py`, `origin_readiness.py`) +
`application/jobs/` (`launcher/checkin.py`, `mint.py`). Web: `webapp/components/ingest/`.

## Fast path — Claude-simulated check-in

When the operator says their data is ready ("just set it up", names a file) **and it loads
cleanly**, author the origin yourself instead of spending the check-in LLM call.

1. **Test the load first.** Registered name → confirm it's in `DATASET_LOADERS`
   (`application/datasets/loaders.py`). Raw file → `POST /datasets/ingest`, or open a
   registered dataset as a draft via `POST /datasets/{name}/draft`. A clean parse + sample
   preview is green. **If the load fails, do NOT simulate** — fall back to the real flow.
2. **Author the origin.** Read the sample rows + answer space and write what `checkin` would:
   the six Layer-1 prompt fields, the 7-field `task_context`, the
   `column_query`/`column_ground_truth` map, a plain `task_description`. Apply via
   `POST /commands/edit-draft-campaign`. The closed-label answer space stays **code-owned** —
   never hand-list it. Every edit lands as a `CommandRecord`, so authorship is on disk.
3. **Gate, then start.** `origin_readiness` must be `complete`, then
   `POST /commands/start-checkin` with `{campaign_id}`.

## Cold-machine bootstrap

Trigger on any of: `.env` missing, backend `/status` unreachable, requested dataset has no
loader.

**Missing `.env`.** Ask for `GROQ_API_KEY` (free tier at console.groq.com). Add
OpenAI/Anthropic/OpenRouter only if named. `.env.example` is the full template.

**Backend `/status` unreachable.** TermNorm is the canonical test backend. If absent,
`git clone https://github.com/runfish5/TermNorm-excel` to `../TermNorm-excel`. Tell the
operator to run `start-server-py-LLMs.bat` in their own terminal; wait for `/status` 200.

**Dataset has no loader.** Two paths:

- **Tenant/client dataset (common):** don't write a loader — ingest it (above). The parser
  (`application/datasets/csv_ingest.py`) handles CSV/TSV/JSON/JSONL/XLSX.
- **New bundled benchmark (rare):** register a loader returning `list[Sample]`
  (`domain/sample.py`) in `DATASET_LOADERS`, and draft
  `datasets/<name>/{pipeline.yaml, campaign.json, dataset.md, prompts/<node>.yaml}` against
  `datasets/bbeh/`. Follow `docs/operations/adding-a-dataset.md` — canonical split first.

## First-run smoke

If `datasets/{name}/` has never produced a measurement (`measurements/runs/{run_id}.jsonl`),
suggest — don't auto-run — `python scripts/smoke_campaign.py --dataset {name}` (~90 s).

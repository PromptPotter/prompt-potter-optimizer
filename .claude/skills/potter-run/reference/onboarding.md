# Onboarding a dataset that has never run

Read this only in **onboarding mode** — a dataset with no campaign, no loader, or a cold
machine. If a campaign already exists, this file is not the one you want.

## The flagship flow — upload → context → origin → select · modify · start

The default for a real client/tenant task (not a bundled benchmark): one chat-shaped flow,
one origin, no hand-written loader. A registered benchmark skips all of it and uses
`new <name>`.

**On a clone or on the team box.** The same flow runs locally (TermNorm `:8000` + PromptPotter
`:8001`) or on the tier-3 deployment, which is the default context (root `CLAUDE.md`).

**Web flow:**

1. Sidebar → "Start a new campaign" opens the IngestPane.
2. Upload CSV / TSV / JSON / JSONL / XLSX, ≤25 MB (`MAX_UPLOAD_BYTES`), ≤50 000 rows
   (`MAX_SAMPLES`) → a DURABLE check-in draft. It is on disk and in the entry list's open
   check-ins from this first action and survives a restart; no campaign exists and nothing
   RUNS until Start mints one. Discard closes it instead.
3. The user types what the prompt is supposed to do. Submitting marks it CONFIRMED.
4. One `checkin` turn proposes the column map (`query` + `ground_truth`), the six decomposed
   Layer-1 prompt fields, and the 7-field `task_context`; code fills the closed-label answer
   space deterministically. A pure checklist gate (no LLM) blocks mint until query +
   ground_truth + framing are all CONFIRMED and every active LLM node owns a model —
   no individual prompt field is gated (the optimizer evolves them).
5. The origin lands as **round 0 / C0** in the lineage forest.
6. Select · modify · start. The optimizer picker chooses which optimizer proposes and its node
   knobs (`optimization.optimizer` + `optimization.nodes.<node>.config.<knob>`). Mint writes the
   tenant dataset + campaign + cycle and runs from round 0.

**CLI parity:**

```bash
python -m promptpotter new <file.csv> --set task_description='what the prompt does'
```

Same chain, same seam: `ingest_draft` → `resolve_origin_turn` → `hold_checkin_start`. Omit
`--set` to let the resolver propose the framing and ask; `--set optimizer=capo` and
`--set nodes.<node>.<knob>=V` are the picker's two answers. A run that stops short of a complete
origin keeps its check-in and names it — the browser's Discard is `discard-draft <draft_id>`.

Seam: `application/datasets/` (`ingest.py`, `origin_resolve.py`, `origin_readiness.py`) +
`application/jobs/` (`launcher/checkin.py`, `mint.py`). Web: `webapp/components/ingest/`.

## Fast path — Claude-simulated check-in

When the operator says their data is ready ("just set it up", names a file) **and it loads
cleanly**, author the origin yourself instead of spending the check-in LLM call.

1. **Test the load first.** Registered name → confirm its `dataset.yaml` `source:` names a recipe in
   `application/datasets/loaders.py::BANK_RECIPES`. Raw file → `POST /datasets/ingest`, or open a
   registered dataset as a draft via `POST /datasets/{name}/draft`. A clean parse + sample
   preview is green. **If the load fails, do NOT simulate** — fall back to the real flow.
2. **Author the origin.** Read the sample rows + answer space and write what `checkin` would:
   the six Layer-1 prompt fields, the 7-field `task_context`, the
   `column_query`/`column_ground_truth` map, a plain `task_description`. Apply via
   `POST /commands/edit-draft-campaign`. The closed-label answer space stays **code-owned** —
   never hand-list it. Every edit lands as a `CommandRecord`, so authorship is on disk.
3. **Gate, then start.** `origin_readiness` must be `complete`, then
   `POST /commands/start-checkin` with `{draft_id}`.

## Cold-machine bootstrap

Trigger on any of: `.env` missing, backend `/status` unreachable, requested dataset has no
loader.

**Missing `.env`.** Ask for `GROQ_API_KEY` (free tier at console.groq.com). Add
OpenAI/Anthropic/OpenRouter only if named. `.env.example` is the full template.

**Backend `/status` unreachable.** TermNorm is the canonical test backend. If absent,
`git clone https://github.com/runfish5/TermNorm-excel` beside this repo and record the path as
`termnorm_checkout` in `CLAUDE.local.md` (`promptpotter/connectors/CLAUDE.md` § TermNorm is not a third party). Start
`start-server-py-LLMs.bat` (who starts it: the autonomy mode, root `CLAUDE.md`); wait for `/status` 200.

**Dataset has no loader.** Two paths:

- **Tenant/client dataset (common):** don't write a loader — ingest it (above). The parser
  (`application/datasets/tabular_ingest.py`) reads the format from the bytes: any-separator text in
  any common encoding, JSON/JSONL, XLSX/XLS/ODS.
- **New fetched benchmark (rare):** register a recipe returning `list[Sample]`
  (`domain/sample.py`) in `BANK_RECIPES`, and draft the tenant definition
  `.promptpotter/projects/{tenant}/datasets/<name>/{dataset.yaml, task.md, origin.yaml, README.md}` —
  a WHOLE document, started from the merged `justlogic/d234`. The package ships templates only. Follow `docs/operations/datasets.md` § Adding a
  dataset — canonical split first.

## First-run smoke

If `{name}` has never produced a measurement (`measurements/cells/{config_key}.jsonl`),
run a one-round campaign (it mints a campaign, so it falls under the autonomy mode):
`python -m promptpotter new {name} --set budget.rounds=1 --set nodes.l1_generate.n_variants=3 --set nodes.adaptive_queue.sp_budget_round=5 --set nodes.escalation.l1_patience=1`.

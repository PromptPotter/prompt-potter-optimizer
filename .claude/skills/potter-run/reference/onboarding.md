# Onboarding a dataset that has never run

**Onboarding mode only** — no campaign, no loader, or a cold machine. A registered benchmark skips all of
it: `new <name>`.

## The flow — upload → context → origin → select · modify · start

One check-in, one origin, no hand-written loader. Web: "+ New campaign" (`#/new`) → upload (≤
`MAX_UPLOAD_BYTES`, ≤ `MAX_SAMPLES` rows) → a durable draft at `#/d/<draft id>` → the user says what the
prompt should do → one `checkin` turn proposes the column map, the prompt fields and `task_context` → a
checklist gate (no LLM) blocks mint until query + ground_truth + framing are CONFIRMED and every active LLM
node owns a model → the origin lands as C0 → the picker sets `optimization.optimizer` and
`optimization.nodes.<node>.config.<knob>` → mint.

CLI parity, same seam (`ingest_draft` → `resolve_origin_turn` → `hold_checkin_start`):

```bash
python -m promptpotter new <file.csv> --set task_description='what the prompt does'
```

Omit `--set` to let the resolver propose the framing and ask; `--set optimizer=capo` and
`--set nodes.<node>.<knob>=V` are the picker's answers. A run stopping short of a complete origin keeps
its check-in; Discard is `discard-draft <draft_id>`. Seam: `application/datasets/` (`ingest.py`,
`origin_resolve.py`, `origin_readiness.py`) + `application/jobs/` (`launcher/checkin.py`, `mint.py`);
web `webapp/components/ingest/`.

## Fast path — Claude-simulated check-in

When the operator says the data is ready **and it loads cleanly**, author the origin yourself:

1. **Test the load first** — raw file → `POST /commands/ingest-upload` (a form); a registered dataset →
   `POST /commands/open-dataset-draft` (its `dataset.yaml` `source:` names a recipe in
   `application/datasets/loaders.py::BANK_RECIPES`); a prior origin → `POST /commands/open-origin-draft`.
   **If the load fails, do NOT simulate.**
2. **Author the origin** via `POST /commands/edit-draft-campaign`: prompt fields, `task_context`, the
   column map, a plain `task_description`. The closed-label answer space stays code-owned.
3. **Gate, then start**: `origin_readiness` `complete`, then `POST /commands/start-checkin` with `{draft_id}`.

## Cold machine

- **Missing `.env`.** Ask for `GROQ_API_KEY`; add OpenAI/Anthropic/OpenRouter only if named. `.env.example` is the template.
- **Backend unreachable.** TermNorm is the canonical test backend: `git clone
  https://github.com/runfish5/TermNorm-excel` beside this repo, record it as `termnorm_checkout` in
  `CLAUDE.local.md` (`promptpotter/connectors/CLAUDE.md` § TermNorm is not a third party), start
  `start-server-py-LLMs.bat` (the autonomy mode says who), wait for `/status` 200.
- **No loader.** A tenant dataset is ingested, never given a loader (`application/datasets/tabular_ingest.py`
  reads any-separator text, JSON/JSONL, XLSX/XLS/ODS). A new fetched benchmark (rare): a `list[Sample]`
  recipe in `BANK_RECIPES` and a dataset folder drafted whole from `justlogic/d234`, per
  `docs/operations/datasets.md` § Adding a dataset.

## First-run smoke

If `{name}` has never produced a measurement (`measurements/cells/{config_key}.jsonl`), one round (it mints,
so the autonomy mode decides):
`python -m promptpotter new {name} --max-rounds 1 --set nodes.l1_generate.n_variants=3 --set nodes.adaptive_queue.sp_budget_round=5 --set nodes.escalation.l1_patience=1`.

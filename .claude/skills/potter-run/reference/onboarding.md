# Onboarding a dataset that has never run

**Onboarding mode only** — no campaign, no loader, or a cold machine. A registered benchmark skips
all of it: `new <name>`.

## The flagship flow — upload → context → origin → select · modify · start

One chat-shaped flow, one origin, no hand-written loader — locally (TermNorm `:8000` + PromptPotter
`:8001`) or on the tier-3 deployment (the default context, root `CLAUDE.md`).

**Web flow:**

1. Sidebar → "+ New campaign" opens a fresh check-in in the chat (`#/new`).
2. Upload CSV / TSV / JSON / JSONL / XLSX, ≤ `MAX_UPLOAD_BYTES`, ≤ `MAX_SAMPLES` rows → a DURABLE
   check-in draft (on disk, survives a restart), listed in the sidebar as an unstarted campaign at
   `#/d/<draft id>`. Nothing RUNS until Start mints; Delete (or Discard) closes it.
3. The user types what the prompt should do → CONFIRMED.
4. One `checkin` turn proposes the column map (`query` + `ground_truth`), the six Layer-1 prompt fields
   and the 7-field `task_context`; code fills the closed-label answer space. A checklist gate (no LLM)
   blocks mint until query + ground_truth + framing are CONFIRMED and every active LLM node owns a
   model — no prompt field is gated (the optimizer evolves them).
5. The origin lands as **round 0 / C0**.
6. Select · modify · start: the picker sets `optimization.optimizer` and
   `optimization.nodes.<node>.config.<knob>`; mint writes tenant dataset + campaign + cycle and runs.

**CLI parity:**

```bash
python -m promptpotter new <file.csv> --set task_description='what the prompt does'
```

Same seam: `ingest_draft` → `resolve_origin_turn` → `hold_checkin_start`. Omit `--set` to let the
resolver propose the framing and ask; `--set optimizer=capo` and `--set nodes.<node>.<knob>=V` are the
picker's answers. A run stopping short of a complete origin keeps and names its check-in; Discard is
`discard-draft <draft_id>`.

Seam: `application/datasets/` (`ingest.py`, `origin_resolve.py`, `origin_readiness.py`) +
`application/jobs/` (`launcher/checkin.py`, `mint.py`). Web: `webapp/components/ingest/`.

## Fast path — Claude-simulated check-in

When the operator says the data is ready **and it loads cleanly**, author the origin yourself instead
of spending the check-in call.

1. **Test the load first.** Registered name → its `dataset.yaml` `source:` names a recipe in
   `application/datasets/loaders.py::BANK_RECIPES`. Raw file → `POST /datasets/ingest`, or a registered
   dataset as a draft via `POST /datasets/{name}/draft`. **If the load fails, do NOT simulate.**
2. **Author the origin**: the six Layer-1 fields, the 7-field `task_context`, the
   `column_query`/`column_ground_truth` map, a plain `task_description`, via
   `POST /commands/edit-draft-campaign` (each edit lands as a `CommandRecord`). The closed-label answer
   space stays **code-owned** — never hand-list it.
3. **Gate, then start**: `origin_readiness` `complete`, then `POST /commands/start-checkin` with
   `{draft_id}`.

## Cold-machine bootstrap

Trigger: `.env` missing, backend `/status` unreachable, or no loader.

**Missing `.env`.** Ask for `GROQ_API_KEY` (free at console.groq.com); add OpenAI/Anthropic/OpenRouter
only if named. `.env.example` is the template.

**Backend unreachable.** TermNorm is the canonical test backend: `git clone
https://github.com/runfish5/TermNorm-excel` beside this repo, record it as `termnorm_checkout` in
`CLAUDE.local.md` (`promptpotter/connectors/CLAUDE.md` § TermNorm is not a third party), start
`start-server-py-LLMs.bat` (who starts it: the autonomy mode), wait for `/status` 200.

**No loader.**

- **Tenant/client dataset (common):** ingest it, don't write a loader —
  `application/datasets/tabular_ingest.py` reads any-separator text in any common encoding,
  JSON/JSONL, XLSX/XLS/ODS.
- **New fetched benchmark (rare):** register a `list[Sample]` recipe (`domain/sample.py`) in
  `BANK_RECIPES` and draft `.promptpotter/projects/{tenant}/datasets/<name>/{dataset.yaml, task.md, origin.yaml, README.md}`
  as a WHOLE document from the merged `justlogic/d234` (the package ships templates only), per
  `docs/operations/datasets.md` § Adding a dataset — canonical split first.

## First-run smoke

If `{name}` has never produced a measurement (`measurements/cells/{config_key}.jsonl`), run one round
(it mints, so the autonomy mode decides):
`python -m promptpotter new {name} --set budget.rounds=1 --set nodes.l1_generate.n_variants=3 --set nodes.adaptive_queue.sp_budget_round=5 --set nodes.escalation.l1_patience=1`.

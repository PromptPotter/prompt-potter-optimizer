# /potter-run — PromptPotter Optimization Campaign

You are PromptPotter's data scientist operator. You run optimization campaigns that find better prompts and pipeline parameters for LLM-powered evaluation pipelines.

## $ARGUMENTS

Optional dataset name (e.g. `bbeh`, `aime_2025`, `gsm8k`, `lca-termnorm`). If omitted, audit the setup and list available datasets. "new campaign" / "start fresh" forces a new session.

---

## CLI Reference

The CLI is two write verbs: `init` creates a session+cycle, `optimize` runs a campaign against it. Reads happen by opening `campaigns/<cycle_id>/{dashboard.json,log.md,index.json}` directly. Stop with Ctrl+C (first finishes in-flight, second force-quits) — there is no mid-run pause/resume.

| Command | Purpose |
|---------|---------|
| `init` | Create session+cycle (`--backend-url`, `--backend-id`, `--config`, `--dataset-name`). Auto-decomposes `datasets/<name>/task_description.md` if present; override via `--task-file` / `--task-text`. |
| `optimize` | Run L1/L2/L3 loop. Resume-by-default; `--from <round>` to rewind. |

---

## Rules (apply throughout)

- **Dataset overrides.** Check `reference/{dataset}-notes.md` first — if present it supersedes this flow.
- **Resume is the default.** `.promptpotter/active_session.json` = `{tenant_id, cycle_id}`. Every command except `init` reads it; if the pointer matches the target, skip `init` and jump to the phase the session needs. Only `init` overwrites the pointer. `init_services()` raises `ActiveSessionMismatchError` on drift unless `take_over=True`.
- **Init is pure prep.** No backend scoring; baseline runs as phase 0 of `optimize`. There is no `--skip-baseline` flag.
- **Timeouts: 30s default, 60s hard max.** Never exceed 60s without asking. Never `run_in_background` CLI commands. If auto-backgrounded, `tasklist | findstr python` → `taskkill //F //PID <pid>` before retrying.
- **Stop when bounded retries exhaust.** `BackendClient.run_query()` auto-retries 429 (RFC 7231 Retry-After) and 5xx/transport errors with countdown backoff (5 attempts max). If a campaign still propagates 5xx after retries, halt and tell the user "Backend returning persistent 5xx — likely Groq upstream rate-limiting or outage. Check and restart." Don't loop on top of the client's loop.
- **Never wipe project data without asking.** Spell out the full path first.
- **Phases 0–0.5 are silent.** The Phase 0.7 outlook is the first thing the user sees — pick the tier that matches state + intent, don't stack sections.
- **Treat defaults as correct.** Documented config (BBEH `campaign.json` vs notebook drift, notebook-driven entry, BBEH inline `task_context`) is expected state, not a warning. Warnings come from the anomaly allowlist in Phase 0.7 — nothing else.
- **Notebook ↔ Claude channel.** Every session has `journal.md` (user narrative) and `notes.md` (your structured notes, tags `[FYI]` / `[RECOMMEND]` / `[BLOCKER]`). Read the journal to pick up intent; append to `notes.md` via Write/Edit so `display.render_claude_notes()` can surface it back.

---

## Configs are the source of truth

Persistent configs decide behavior — the skill does not carry a parallel default-ladder.

- `datasets/{name}/dataset.md` — init flags, entry point (CLI vs notebook)
- `datasets/{name}/campaign.json` — hyperparameters (max_rounds, n_variants, sp_budget_ttest, patiences)
- `datasets/{name}/pipeline.json` — pipeline + model + caps
- BBEH only: `notebooks/bbeh_potter.ipynb::build_campaign_config()` shadows `campaign.json`; notebook wins
- Active session: `.promptpotter/active_session.json` → `campaigns/{cycle_id}/index.json` + `dashboard.json`

Per-dataset reasoning defaults (model + `reasoning_effort` + `max_tokens`) live in [`reference/dataset-reasoning-matrix.md`](reference/dataset-reasoning-matrix.md). **Groq daily-volume swap:** `openai/gpt-oss-120b` is the canonical model; during dev the operator may flip the `pipeline.json` `model` field to `openai/gpt-oss-20b` when 120b daily volume is exhausted. Treat the field as a live operator knob, not a fixed default. `max_tokens` is never set numerically in node configs — provider ceiling applies; operators override per-cycle via `campaign.json::pipeline_overrides`.

Read these. Don't recommend parameter tweaks unless the user asks. Don't classify data volume ("minimal"/"substantial") or propose leaderboard picks unbidden.

**Reading per-query display lines:** when the dataset loader assigns `sample_id` (BBEH today), each line carries a `#NNN` column right after the time — e.g. `0.0s #042 MISS [ai]📖 -> 'unknown' gt:'disproved' q:'…'`. Use the ID to refer to specific samples across runs.

## Phase −1: Bootstrap (only on cold start, otherwise silent)

Trigger if any of: `.env` missing, backend `/status` unreachable, or requested dataset has no loader. Run sub-flows in order; each one's success unblocks the next phase.

**Missing `.env`.** Ask the operator for `GROQ_API_KEY` (free tier at [console.groq.com](https://console.groq.com) is the default optimizer LLM). Add OpenAI/Anthropic/OpenRouter only if explicitly named. Write `.env` with `GROQ_API_KEY=…` + `LLM_MODEL=openai/gpt-oss-120b`; `.env.example` is the full template.

**Backend `/status` unreachable.** TermNorm is the canonical test backend. If it isn't local yet, offer `git clone https://github.com/runfish5/TermNorm-excel` to `../TermNorm-excel` (sibling of PromptPotter; operator can override the path). Once present, tell the operator to run `start-server-py-LLMs.bat` in their own terminal — same hand-off model as Phase 4 (`optimize`). Wait for `/status` 200 before continuing. *Future improvement: spawn a dedicated terminal automatically once that capability lands.*

**Dataset has no loader.** Anything already in `promptpotter/application/datasets/datasets.py::DATASET_LOADERS` (bundled benchmarks + `lca-termnorm` via `load_excel_ground_truth`) needs nothing — `init` picks them up. **New dataset:** the skill writes a custom loader for the operator. Vocabulary: a function returning `list[Sample]` (`promptpotter/domain/sample.py` — `query` + `ground_truth` + optional `id`/extras). Read the operator's data shape (CSV, Excel, JSON, HuggingFace, …), generate `load_<name>(...)`, and register it in `DATASET_LOADERS`. Then draft `datasets/<name>/{pipeline.json, campaign.json, dataset.md, prompts/<node>.json}` against the patterns in `datasets/bbeh/`. The operator just describes their data and answer keys — Claude does the wiring.

## Phase 0: Audit (silent)

1. Read `datasets/{name}/dataset.md` + `campaign.json` + `pipeline.json` (+ BBEH notebook).
2. `curl -s {backend_url}/status` — backend up?
3. Active pointer → `index.json` + `dashboard.json` if present.

**Print only if** no dataset arg, dataset not implemented (scorer in `promptpotter/application/scoring/formula.py::SCORING_FUNCTIONS`, loader in `promptpotter/application/datasets/datasets.py::DATASET_LOADERS`), or an anomaly from the allowlist below fires.

If `datasets/{name}/` has never produced a `dataset_runs/` entry, suggest (don't auto-run): `python scripts/smoke_campaign.py --dataset {name}` (~90s).

## Phase 0.7: Outlook

Default shape: one sentence, one compact box, or 3–5 bullets — or a combination. Two modes:

**No active session** — one sentence: which entry point the dataset uses (per `dataset.md`) and what to run. No box, no ask.

**Active session** — compact 4–6 line box mirroring `dashboard.json` (`cycle_id · dataset · phase · round/max · best vs baseline`) + one sentence: resume command or next phase.

If the user's intent is genuinely ambiguous ("should I resume or start over?"), ask once — one question, no `(a)/(b)` stacking.

### Anomaly allowlist (the only sources of warnings)

- Backend `/status` non-200 or connection refused
- `llm_ranking` in active_nodes on TermNorm
- Active-session pointer points at a different dataset than requested
- Recent `dataset_runs/*.json` show empty `predicted` strings (BBEH regression)
- `ActiveSessionMismatchError` on `init_services()`

Surface anomalies as a one-line flag at the top, then the normal outlook. Never warn about documented config (notebook-driven datasets, `campaign.json` ↔ notebook drift, BBEH inline `task_context`, data volume) — that's expected state.

---

## Phase 1: Initialize (skip if resuming)

Flags from `datasets/{name}/dataset.md § Init Flags` — verbatim, never guess.

```bash
python -m promptpotter init {flags from dataset.md}
```

Foreground, 30s timeout. `init` auto-decomposes `datasets/{name}/task_description.md` if present (override via `--task-file` / `--task-text`). If `llm_ranking` lands in active nodes for `lca-termnorm`, STOP — wrong config.

## Phase 4: Optimize

**The user runs `python -m promptpotter optimize` in their own terminal** — campaigns take minutes to hours. Your job: prep Phase 0 + 1, then let them launch. On their return, read `dashboard.json` (live state) + the latest `trials/trial_NNNN.json` (round summary, critique, leaderboard) and summarize per round:

```
ROUND {N} COMPLETE
  Winner:   C{x}/{total} — {acc}% ({delta} vs prev best)
  Layer:    {L1/L2/L3}    Patience: {x}/{max}
  Queries:  {evaluated}   Cache: {hit_rate}%

CRITIQUE: {2-4 key lines — what failed, what to try next}
NEXT:     {continue L1 / escalate to L2 / etc.}
```

**Monitor** by tailing `dashboard.json`. Diagnose via `trials/trial_NNNN.json` (round summary + L1 critique), `.cache/rounds/round_NNNN.json` (per-round node I/O — internal), and `output.log` (per-query HIT/MISS). Stop with Ctrl+C — first finishes in-flight, second force-quits. Re-run `optimize` to resume.

**Incremental persistence.** Every query lands in `library/dataset_runs/` immediately — hard kills lose zero work, resume auto cache-hits prior results.

Escalation model: `reference/optimization-layers.md`, `docs/concepts/three-layer-loop.md`, `docs/concepts/self-healing.md`.

## Phase 5: Results

Open `campaigns/<cycle_id>/log.md` (rendered digest with status, per-round critique / L2 directive / changes, hard-samples heatmap, final winner) and `index.json::final` (structured: `winner_prompt_fields`, `winner_pipeline_params`, `best_accuracy`, `baseline_accuracy`, `stop_reason`). `index.json::final.stop_reason` → recovery path in `reference/troubleshooting.md`.

---

## Operator style

- **Default shape**: one sentence, one compact box, or 3–5 bullets — or a combination. Anomaly flag + outlook is the only stacking allowed.
- **Defer to configs.** Report what's on disk; don't second-guess the configured hyperparameters, data volume, or pipeline choice unless the user asks.
- Interpret results, don't dump CLI output.
- **Kill command is situational** — surface `tasklist | findstr python → taskkill //F //PID <pid>` only when recommending a long-running launch in *this* turn.
- Error prefixes (`[CLIENT]` / `[SERVER]` / `[CONNECTION]` / `[PIPELINE]`) → check `output.log` and the latest `trials/trial_NNNN.json` in the campaign dir.
- Respect user-specified timeouts/stop methods exactly; ask before assuming.

---

## References

- `reference/bbeh-notes.md` — BBEH overrides (notebook-driven, single global prompt)
- `reference/benchmark-datasets.md` — readiness + cost model
- `reference/optimization-layers.md` — L1/L2/L3 escalation
- `reference/troubleshooting.md` — stop-reason recovery
- `docs/concepts/three-layer-loop.md`, `docs/concepts/self-healing.md`, `docs/operations/cli-reference.md`

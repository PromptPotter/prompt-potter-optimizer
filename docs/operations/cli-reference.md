# CLI Reference

Two write verbs: `new` and `resume`. Reads happen by opening the on-disk artifact tree — no read CLI.

```bash
python -m promptpotter [--tenant <id>] <subcommand> [options]
```

`--tenant` (default `"default"`) selects the partition under `.promptpotter/projects/`.

State files: [`persistence-and-state.md`](persistence-and-state.md). Rewind / fork: same page.

---

## The two write verbs

| Verb | Behavior |
|------|----------|
| **`new <name\|file>`** | Mint a fresh session+cycle and run from round 0. A **dataset name** uses an already-authored `datasets/<name>/`. A **raw file** (CSV, columns named anything) is ingested → AI origin check-in → committed as a tenant dataset → run — the headless twin of the web onboarding; it builds that origin for you and resolves every once-hidden default to a confirmed, on-disk value before minting. Every invocation mints a fresh root cycle; on content-hash collision with an existing root, the `cycle_id` gets a `_r2` / `_r3` discriminator suffix so the new run lands in its own directory tree (separate dashboard, log, archive subtree). The prior campaign is preserved. |
| **`resume`** | Pick up the active session at the latest completed round (or rewind/fork per the flags below). |

Both `new <slug>` and `resume` operate on **tenant-ingested** datasets
(`projects/{tenant}/datasets/{slug}/`), not just repo benchmarks — dataset config
resolves tenant-first (see `resolve_dataset_config_dir`). So a file you ingest once
with `new data.csv` is re-runnable forever after by its slug.

---

## new — fresh mint (dataset name *or* raw file)

### Name form — an authored dataset

```bash
python -m promptpotter new lca-termnorm \
    --backend-url http://127.0.0.1:8000
```

Connects to the backend, fetches pipeline schema via `GET /pipeline`, applies `exclude_nodes` and `pipeline_overrides` from config, mints session+cycle, then runs the optimization loop from round 0.

If `<dataset>/task_description.md` exists, the task is decomposed once into `task_context` and stored on the session. Disk-cached, so subsequent `new` runs on the same dataset don't re-pay the decomposition cost.

### File form — a raw CSV (origin check-in folds in)

```bash
python -m promptpotter new data/lab_tests.csv \
    --set task_description="map each lab-test name to its code" \
    --backend-url http://127.0.0.1:8000
```

When the positional is a **file** (`Path.is_file()`), `new` parses it into a draft, runs the AI origin check-in (the same `checkin/2` node the web ingest uses), auto-confirms high-confidence findings, then — once the deterministic readiness gate passes — **commits a tenant dataset and runs the loop inline**, exactly as the name form does (rich terminal display). It reuses the exact orchestration behind the web onboarding (`ingest_draft` → `resolve_origin_until_gated` → `commit_draft_to_dataset`); the file branch adds no parallel mint logic.

If a gap survives the resolver (e.g. an ambiguous column or unstated framing), `new` prints the open fields + the resolver's questions and exits non-zero — nothing is minted with a guessed default. Confirm the field with `--set` and re-run. After a successful run the committed slug is a first-class dataset: `new <slug>` / `resume` operate on it directly.

| Flag | Purpose |
|---|---|
| `<name\|file>` (positional) | Dataset name under `./datasets/` (auto-loads its `campaign.json`) **or** a path to a raw file (CSV) to ingest |
| `--config` | Campaign config JSON file — overrides the dataset's default `campaign.json` (name form) |
| `--dataset-name` | Alternative to the positional name |
| `--slug` | *(file form)* Dataset slug under `projects/{tenant}/datasets/` (default: derived from the filename) |
| `--set FIELD=VALUE` | *(file form)* Confirm an origin field directly (operator-stated), repeatable. Fields: `task_description`, `column.query`, `column.ground_truth`, `connector`, `scoring_composite`, `max_rounds`, `optimizer.provider`, `optimizer.model`. Applied before the resolver, so it seeds the rest |
| `--backend-url` | Backend service URL |
| `--backend-id` | Override backend id (auto-derived from `dataset_name` otherwise) |
| `--task-file` | *(name form)* Override `<dataset>/task_description.md` |
| `--task-text` | *(name form)* Override `<dataset>/task_description.md` inline |
| `--halt-at` / `--spend-budget` | Run-halt gates (both forms) |

---

## resume — pick up the active session

```bash
python -m promptpotter resume                 # resume from latest completed round
python -m promptpotter resume --from <round>  # rewind in place
```

Runs the full autonomous loop (L1 → L2 → L3 until convergence or `max_rounds`). State is checkpointed between rounds.

While `resume` runs, the live in-flight round mirrors into the active cycle's own `campaigns/{campaign_id}/cycles/{cycle_id}/dashboard.json::current_round`. Each completed round is snapshotted to the same cycle's `rounds/round_NNNN.json`.

| Flag | Purpose |
|---|---|
| `--from <round>` | Rewind the active cycle to after round N before resuming |
| `--no-check` | On resume, rescore but skip the decision-replay halt |
| `--fork-on-divergence` | On *data-affecting* config divergence (scoring, optimizer_llm, pipeline_overrides, exclude_nodes, dataset_name), mint a sibling cycle (with `parent_cycle_id`) and re-run the divergent round under the current scorer. Policy-only edits (PoBB knobs, patience, thresholds, n_variants, exploration) continue in-place — no fork, the flag is a no-op. See `CampaignConfig.classify_diff_against` for the field-by-field split. |

---

## Reading state

No read CLI. Everything is per-cycle — open the cycle dir you're running:

| File | Purpose |
|---|---|
| `<cycle_dir>/dashboard.json` | Live scalar state (phase, round, candidate, in-flight payload, per-round node I/O). `cycle_id` field stamps this cycle. |
| `<cycle_dir>/output.log` | Append-only HIT/MISS history. `=== FORK ... ===` banner inline at each cutover. |
| `<cycle_dir>/log.md` | Per-round digest, regenerated on every round-complete and at finalize |
| `<cycle_dir>/index.json` | Campaign metadata + `final` block. Forks have a `parent_cycle_id` field. |
| `<cycle_dir>/rounds/round_NNNN.json` | Per-round optimizer checkpoint |
| `<cycle_dir>/.runtime/cache/rounds/round_NNNN.json` | Per-round node I/O (internal) |

`<cycle_dir>` resolves to `campaigns/{campaign_id}/cycles/{cycle_id}/` for every cycle — root, fork, diag, sweep, all flat under `cycles/`. Each cycle owns its own `dashboard.json` + `output.log` (a fork's is seeded from its parent at the cut).

---

## Worked example

```bash
python -m promptpotter new lca-termnorm \
    --backend-url http://127.0.0.1:8000
# Open campaigns/<campaign_id>/cycles/<cycle_id>/{dashboard.json, log.md, index.json} in your editor.
```

---

## Interrupt handling

- **First Ctrl+C** — finishes the in-flight backend call, saves all completed work, exits cleanly.
- **Second Ctrl+C** — force-quits immediately.

After an interrupted run, check for orphan processes (`tasklist | findstr python` on Windows; `ps aux | grep python` on Linux/Mac).

---

## Pipeline params threading

`configure_and_apply_pipeline(session, campaign_config)` applies `exclude_nodes` and `pipeline_overrides` and returns `pipeline_params`, which flows unchanged through both `new` and `resume`. If `pipeline_params` is `None`, the backend runs the full pipeline.

---

## Zero-signal sample filtering

Off by default. Queries with variance 0 (always-hit or always-miss) across at least one observation are physically moved from `datasets/{name}.json::items` into `datasets/{name}.json::excluded` after each round.

Enable via `optimization.zero_signal_filter_enabled: true` in `campaign.json`.

```bash
# Inspect what's been excluded
cat .promptpotter/projects/{backend_id}/datasets/{name}.json \
  | jq '.excluded | map({query: .item.query, hit_rate, observations, reason})'
```

Restoration is manual — move entries from `excluded` back into `items`.

---

## Environment

`.env` file (see `.env.example`) carries API keys. Provider selection lives on `CampaignConfig.optimizer_llm.provider` per dataset's `campaign.json` — no env-var default.

| Variable | When required | Purpose |
|----------|---------------|---------|
| `GROQ_API_KEY` | using Groq | Groq API key |
| `OPENAI_API_KEY` | using OpenAI | OpenAI API key |
| `ANTHROPIC_API_KEY` | using Anthropic | Anthropic API key |
| `OPENROUTER_API_KEY` | using OpenRouter | OpenRouter (`sk-or-…`) |
| `LLM_MODEL` | always | Default model when `optimizer_llm.model` is null (e.g. `openai/gpt-oss-120b`) |
| `LANGFUSE_PUBLIC_KEY` | optional | Langfuse cloud tracing |
| `LANGFUSE_SECRET_KEY` | optional | Langfuse cloud tracing |
| `LANGFUSE_HOST` | optional | Langfuse host URL |

### Optional dependency bundles

```bash
pip install -e ".[stats]"          # Wilson CI, significance tests (scipy)
pip install -e ".[jupyter]"        # JupyterLab + IPython display
pip install -e ".[benchmarks]"     # GSM8K, AIME 2025, BBEH (HuggingFace datasets)
pip install -e ".[observability]"  # Langfuse cloud tracing
pip install -e ".[anthropic]"      # Anthropic Claude as optimizer LLM
pip install -e ".[dev]"            # pytest, ruff, mypy, deptry
pip install -e ".[all]"            # Everything except [dev]
pip install -e ".[all,dev]"        # Everything — recommended for contributors
```

Groq daily-volume model swap (when `120b` exhausts): [`../manual/05-troubleshooting.md § Groq daily token limit exhausted on 120b`](../manual/05-troubleshooting.md).

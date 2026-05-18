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
| **`new <name>`** | Mint a fresh session+cycle from `datasets/<name>/`, decompose the task description on first sight, run from round 0. Every invocation mints a fresh root cycle; on content-hash collision with an existing root, the `cycle_id` gets a `_r2` / `_r3` discriminator suffix so the new run lands in its own directory tree (separate dashboard, log, archive subtree). The prior campaign is preserved. |
| **`resume`** | Pick up the active session at the latest completed round (or rewind/fork per the flags below). |

---

## new — fresh mint

```bash
python -m promptpotter new lca-termnorm \
    --backend-url http://127.0.0.1:8000
```

Connects to the backend, fetches pipeline schema via `GET /pipeline`, applies `exclude_nodes` and `pipeline_overrides` from config, mints session+cycle, then runs the optimization loop from round 0.

If `datasets/<name>/task_description.md` exists, the task is decomposed once into `task_context` and stored on the session. Disk-cached, so subsequent `new` runs on the same dataset don't re-pay the decomposition cost.

| Flag | Purpose |
|---|---|
| `<name>` (positional) | Dataset under `./datasets/` — auto-loads `datasets/<name>/campaign.json` |
| `--config` | Campaign config JSON file — overrides the dataset's default `campaign.json` |
| `--dataset-name` | Alternative to the positional `<name>` |
| `--backend-url` | Backend service URL |
| `--backend-id` | Override backend id (auto-derived from `dataset_name` otherwise) |
| `--task-file` | Override `datasets/<name>/task_description.md` |
| `--task-text` | Override `datasets/<name>/task_description.md` inline |

---

## resume — pick up the active session

```bash
python -m promptpotter resume                 # resume from latest completed round
python -m promptpotter resume --from <round>  # rewind in place
```

Runs the full autonomous loop (L1 → L2 → L3 until convergence or `max_rounds`). State is checkpointed between rounds.

While `resume` runs, the live in-flight round mirrors into `campaigns/{root_cycle_id}/dashboard.json::current_round`. Each completed round is snapshotted to `campaigns/{cycle_id}/rounds/round_NNNN.json`.

| Flag | Purpose |
|---|---|
| `--from <round>` | Rewind the active cycle to after round N before resuming |
| `--no-check` | On resume, rescore but skip the decision-replay halt |
| `--fork-on-divergence` | On *data-affecting* config divergence (scoring, optimizer_llm, pipeline_overrides, exclude_nodes, dataset_name), mint a sibling cycle (with `parent_cycle_id`) and re-run the divergent round under the current scorer. Policy-only edits (PoBB knobs, patience, thresholds, n_variants, exploration) continue in-place — no fork, the flag is a no-op. See `CampaignConfig.classify_diff_against` for the field-by-field split. |

---

## Reading state

No read CLI. Two bands — telemetry at the family root, audit per cycle:

| File | Purpose |
|---|---|
| `campaigns/<root_cycle_id>/dashboard.json` | Live scalar state (phase, round, candidate, in-flight payload, per-round node I/O). `cycle_id` field names the active fork. |
| `campaigns/<root_cycle_id>/output.log` | Append-only HIT/MISS history. `=== FORK ... ===` banner inline at each cutover. |
| `<cycle_dir>/log.md` | Per-round digest, regenerated on every round-complete and at finalize |
| `<cycle_dir>/index.json` | Campaign metadata + `final` block. Forks have a `parent_cycle_id` field. |
| `<cycle_dir>/rounds/round_NNNN.json` | Per-round optimizer checkpoint |
| `<cycle_dir>/.cache/rounds/round_NNNN.json` | Per-round node I/O (internal) |

`<cycle_dir>` resolves to `campaigns/{cycle_id}/` for root cycles and `campaigns/{root_cycle_id}/forks/{cycle_id}/` for forks. Telemetry stays at the family root regardless.

---

## Worked example

```bash
python -m promptpotter new lca-termnorm \
    --backend-url http://127.0.0.1:8000
# Open campaigns/<cycle_id>/{dashboard.json, log.md, index.json} in your editor.
```

---

## Interrupt handling

- **First Ctrl+C** — finishes the in-flight backend call, saves all completed work, exits cleanly.
- **Second Ctrl+C** — force-quits immediately.

After an interrupted run, check for orphan processes (`tasklist | findstr python` on Windows; `ps aux | grep python` on Linux/Mac).

---

## Pipeline params threading

`configure_pipeline(svc, campaign_config)` applies `exclude_nodes` and `pipeline_overrides` and returns `pipeline_params`, which flows unchanged through both `new` and `resume`. If `pipeline_params` is `None`, the backend runs the full pipeline.

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
pip install -e ".[excel]"          # Excel dataset loading
pip install -e ".[benchmarks]"     # GSM8K, AIME 2025, BBEH (HuggingFace datasets)
pip install -e ".[observability]"  # Langfuse cloud tracing
pip install -e ".[anthropic]"      # Anthropic Claude as optimizer LLM
pip install -e ".[dev]"            # pytest, ruff, mypy, deptry
pip install -e ".[all]"            # Everything except [dev]
pip install -e ".[all,dev]"        # Everything — recommended for contributors
```

Groq daily-volume model swap (when `120b` exhausts): [`../manual/05-troubleshooting.md § Groq daily token limit exhausted on 120b`](../manual/05-troubleshooting.md).

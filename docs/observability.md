# Observability

This guide covers Langfuse integration, MLflow (opt-in), and how to navigate the accumulated research data that PromptPotter writes during optimization campaigns.

All per-campaign evaluation data is stored locally first, under `.promptpotter/projects/{tenant_id}/campaigns/{cycle_id}/`. Cloud Langfuse is optional — you can run campaigns without it and push data later. MLflow is also optional and **off by default**; set `MLFLOW_ENABLED=true` in `.env` to log per-round runs.

---

## Start Here: `events.jsonl`

`campaigns/{cycle_id}/events.jsonl` is a flat, human-readable log where every significant action is one JSON line. This is a custom extension (not part of the Langfuse or MLflow spec).

### Format

Each line is a self-contained JSON object with `event`, `timestamp`, and navigation IDs:

```jsonl
{"event": "dataset_run", "trace_id": "260225...", "run_id": "baseline_816203b2", "accuracy": 0.75, "hits": 30, "total": 40, "timestamp": "2026-02-25T14:00:00Z"}
{"event": "campaign_start", "trace_id": "260225...", "campaign_id": "campaign_abc", "baseline_accuracy": 0.75, "timestamp": "2026-02-25T14:01:00Z"}
{"event": "round_complete", "trace_id": "260225...", "campaign_id": "campaign_abc", "round": 0, "accuracy": 0.80, "improved": true, "next_action": "generate", "timestamp": "2026-02-25T14:05:00Z"}
{"event": "prompt_version", "prompt_fields_id": "abc12345", "family": "optimizer_prompt", "parent_id": "def67890", "timestamp": "2026-02-25T14:05:01Z"}
```

### Navigating from events.jsonl

| Field in event | Navigate to | What you'll find |
|---------------|-------------|-----------------|
| `trace_id` | `langfuse/traces/{trace_id}.json` | Full trace metadata (input, output, tags) |
| `trace_id` | `langfuse/observations/{trace_id}/` | Detailed step-by-step observations |
| `trace_id` | `langfuse/scores/{trace_id}.jsonl` | Evaluation scores over time |
| `prompt_fields_id` | `prompts/optimizer_prompt/{id_prefix}/` | Rendered prompt text + Layer 1 field metadata |

Out-of-campaign emits (baseline, recon, historical backfill) have no bound `cycle_id`; they land in a shared `library/obs/` pool using the same tree shape.

---

## MLflow (opt-in)

MLflow logging is **off by default**. Enable it by setting `MLFLOW_ENABLED=true` in `.env`; install the extra:

```bash
pip install -e ".[observability]"
```

When enabled, FileSink logs each round as an MLflow run via the Python SDK. Runs are written to `library/mlruns/` (tenant-global, shared across campaigns). Experiments are named `{tenant_id}/{cycle_id}`.

```bash
# View runs
mlflow ui --backend-store-uri "file://$(pwd)/.promptpotter/projects/{tenant_id}/library/mlruns"
# Opens at http://localhost:5000
```

**What you'll see in mlflow ui:**
- One experiment per campaign (named `{tenant_id}/{cycle_id}`)
- One run per optimization round (params: model, temperature, n_variants, round)
- Metrics: accuracy, hits, total
- Tags: improved, next_action, winner_prompt_fields_id

---

## Langfuse Cloud Integration

In addition to file-based traces, PromptPotter can push eval runs to Langfuse cloud.

### Setup

Add to `.env`:

```
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

### Live tracing (during optimization)

When credentials are set **before** running a feedback cycle, traces are pushed to Langfuse in real-time. Each campaign creates a trace with:
- A root `chain` observation (triggers the pipeline graph visualization)
- Per-round `span` observations with real start/end times
- Per-evaluation `tool` observations nested under their round
- Accuracy scores attached to each round

### Retroactive push

If you ran an optimization campaign without Langfuse credentials, all evaluation data is still on disk. Push it after the fact:

```python
from promptpotter.display.campaign import configure_langfuse, push_langfuse

# 1. Enable Langfuse (if not already in .env)
configure_langfuse(
    enabled=True,
    public_key="pk-lf-...",
    secret_key="sk-lf-...",
)

# 2. Push all accumulated data
stats = push_langfuse(svc["store"], svc["backend_id"])
```

This creates one trace per dataset run, registers a ground-truth dataset with all queries, and links each evaluation to its dataset item. Re-running is safe — already-pushed runs are skipped.

### Re-pushing after clearing Langfuse

The per-cycle langfuse id map is stored at `campaigns/{cycle_id}/langfuse/state.json`. To force re-push for a campaign, delete that file and re-run the push; for historical backfill state, delete `library/obs/langfuse/backfill_state.json` if present.

---

## File Layout Reference

```
.promptpotter/projects/{tenant_id}/
  campaigns/{cycle_id}/
    events.jsonl                           # START HERE — flat navigation log
    langfuse/
      state.json                           # id maps (campaign→trace, …) persisted across resume
      traces/{trace_id}.json               # one file per trace
      observations/{trace_id}/{obs_id}.json
      scores/{trace_id}.jsonl              # accuracy scores
      datasets/{dataset_name}/{item_id}.json
    prompts/
      optimizer_prompt/{version}/
        metadata.json                      # version metadata + layer1 fields
        prompt.txt                         # rendered prompt text
  library/
    mlruns/                                # MLflow tracking (opt-in, shared across cycles)
    obs/                                   # orphan-event pool (out-of-campaign emits)
      events.jsonl
      langfuse/{traces,observations,scores,datasets}/
      prompts/
```

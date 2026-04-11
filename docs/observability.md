# Observability

This guide covers Langfuse integration and how to navigate the accumulated research data that PromptPotter writes to `obs/` during optimization campaigns.

All evaluation data is stored locally first (file-based traces in `.promptpotter/projects/{backend_id}/obs/`). Cloud Langfuse is optional — you can run optimization campaigns without it and push data later.

---

## Start Here: `events.jsonl`

`obs/langfuse/events.jsonl` is a flat, human-readable log where every significant action is one JSON line. This is a custom extension (not part of the Langfuse or MLflow spec).

### Format

Each line is a self-contained JSON object with `event`, `timestamp`, and navigation IDs:

```jsonl
{"event": "dataset_run", "trace_id": "260225...", "run_id": "baseline_816203b2", "accuracy": 0.75, "hits": 30, "total": 40, "model": "llama-4-maverick", "timestamp": "2026-02-25T14:00:00Z"}
{"event": "campaign_start", "trace_id": "260225...", "campaign_id": "campaign_abc", "baseline_accuracy": 0.75, "timestamp": "2026-02-25T14:01:00Z"}
{"event": "round_complete", "trace_id": "260225...", "campaign_id": "campaign_abc", "round": 0, "accuracy": 0.80, "improved": true, "next_action": "generate", "timestamp": "2026-02-25T14:05:00Z"}
{"event": "prompt_version", "prompt_state_id": "abc12345", "family": "ranking_prompt", "parent_id": "def67890", "timestamp": "2026-02-25T14:05:01Z"}
```

### Navigating from events.jsonl

| Field in event | Navigate to | What you'll find |
|---------------|-------------|-----------------|
| `trace_id` | `langfuse/traces/{trace_id}.json` | Full trace metadata (input, output, tags) |
| `trace_id` | `langfuse/observations/{trace_id}/` | Detailed step-by-step observations |
| `trace_id` | `langfuse/scores/{trace_id}.jsonl` | Evaluation scores over time |
| `campaign_id` | `experiments/{campaign_id}/meta.yaml` | MLflow experiment metadata |
| `campaign_id` | `experiments/{campaign_id}/{run_id}/` | Per-round MLflow runs with params/metrics |
| `prompt_state_id` | `prompts/ranking_prompt/{id_prefix}/` | Rendered prompt text + Layer 1 field metadata |

---

## MLflow Viewer Setup

PromptPotter has **zero MLflow dependency**. It writes experiment data in MLflow FileStore format using pure JSON/YAML file I/O. To visualize the data, use a separate throwaway Python environment:

```bash
# Create isolated viewer environment (do NOT install mlflow in PromptPotter's venv)
python -m venv C:\temp\mlflow-viewer\venv
C:\temp\mlflow-viewer\venv\Scripts\activate
pip install mlflow

# Point at the experiment data PromptPotter wrote
mlflow ui --backend-store-uri file:./.promptpotter/projects/{backend_id}/obs/experiments
# Opens at http://localhost:5000
```

This pattern is proven in production.

**What you'll see in mlflow ui:**
- One experiment per optimization campaign
- One run per optimization round (with params: model, temperature, n_variants)
- Metrics: accuracy, hits, total (time-series per round)
- Tags: provider, next_action, improved

---

## Langfuse Cloud Integration

In addition to file-based traces, PromptPotter can push eval runs to Langfuse cloud via `obs/langfuse_push.py`.

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

If you delete traces/datasets in the Langfuse UI and want to re-push everything, delete the local state file first:

```python
import os
state_path = os.path.join(
    svc["store"].base_dir, svc["backend_id"],
    "obs", "langfuse", "backfill_state.json",
)
os.remove(state_path)
stats = push_langfuse(svc["store"], svc["backend_id"])
```

---

## File Layout Reference

```
.promptpotter/projects/{backend_id}/obs/
  langfuse/
    events.jsonl                          # START HERE — flat navigation log
    traces/{trace_id}.json                # One file per trace
    observations/{trace_id}/{obs_id}.json # Nested observations
    scores/{trace_id}.jsonl               # Accuracy scores
  experiments/                            # MLflow FileStore format
    {campaign_id}/
      meta.yaml                           # Experiment metadata
      {run_id}/
        meta.yaml                         # Run metadata
        params/{param_name}               # Parameter files
        metrics/{metric_name}             # Metric time-series
        tags/{tag_name}                   # Tags
  prompts/
    {prompt_family}/
      {version}/
        metadata.json                     # Version metadata + template_variables
        prompt.txt                        # Rendered Layer 1 prompt text
```

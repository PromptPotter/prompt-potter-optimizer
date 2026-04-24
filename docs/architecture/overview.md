# Architecture

Navigation hub for `docs/architecture/`. Read `introduction.md` and `how-a-campaign-runs.md` first if you're new — this page links out, it doesn't explain.

---

## Entry Points

Four ways to drive a campaign, in maturity order (features land left → right):

1. **Notebook** — `notebooks/optimization_campaign.ipynb` — primary, daily driver.
2. **CLI** — `python -m promptpotter` — `init → [set-task] → optimize → show-results`.
3. **FastAPI** — `promptpotter/main.py` — currently read-only API.
4. **Next.js webapp** — planned (M10/M11).

---

## Persistence

Sessions and campaigns are separate concepts. A session is the operator workspace; a campaign is one optimization cycle inside it. The active cycle is recorded in `.promptpotter/active_session.json` (single source of truth for all commands).

```
.promptpotter/
  active_session.json                  # { tenant_id, session_id, cycle_id } pointer
  projects/{tenant_id}/
    sessions/{session_id}/             # per-session: operator workspace
      session.json                     # session metadata
      journal.md / notes.md            # notebook ↔ Claude exchange
      control.json                     # HITL pause/resume
    campaigns/{cycle_id}/              # per-cycle: all artifacts for one optimization
      index.json                       # campaign metadata + trial index
      dashboard.json                   # live counters
      output.log / log.md
      trials/trial_NNNN.json           # resume source of truth
      candidates/round_NNNN.json       # pre-scoring checkpoint
      rounds/round_NNN.json            # per-round LLM action audit
      events.jsonl                     # observability mirror (not read for state)
      langfuse/                        # trace persistence
      prompts/{family}/{version}/      # rendered optimizer prompts
      archived/resumed_at_{ts}/        # mid-cycle rewind history
    library/                           # cross-cycle: shared reference data
      backends/{backend_id}/           # backend profile + datasets
      dataset_runs/                    # content-addressed evaluation archive
      search_memory.json               # materialized intelligence view
      prompt_aliases.json
      restructure_cache.json
```

Prior evaluation results are replayed without calling the backend when a new pipeline configuration shares a matching prefix with a stored run. `events.jsonl` is a pure observability mirror — nothing reads it for state reconstruction. Resume and rewind are driven entirely by `trials/trial_NNNN.json`.

---

## Where to Read Next

**New to PromptPotter:** [`../introduction.md`](../introduction.md) → [`../how-a-campaign-runs.md`](../how-a-campaign-runs.md) → [`optimization.md`](optimization.md)

**Extending the system:** [`node-standard.md`](node-standard.md) → [`information-flow.md`](information-flow.md) → [`prompt-scheme.md`](prompt-scheme.md)

**Debugging a campaign:** [`../troubleshooting.md`](../troubleshooting.md) → [`optimization.md § Self-healing`](optimization.md) → [`search-memory-intelligence.md`](search-memory-intelligence.md)

| Document | What it covers |
|----------|---------------|
| [`optimization.md`](optimization.md) | Full loop mechanics — L1/L2/L3, escalation, self-healing, elimination, resume |
| [`scoring-policy.md`](scoring-policy.md) | Traces vs. scores, rescore-on-load, decision-replay, fork |
| [`prompt-scheme.md`](prompt-scheme.md) | The 8-field prompt decomposition, two prompt stores, rendering pipeline |
| [`information-flow.md`](information-flow.md) | What each optimizer layer reads and writes |
| [`node-standard.md`](node-standard.md) | Node capabilities, wiring a new node, pipeline declaration format |
| [`search-memory-intelligence.md`](search-memory-intelligence.md) | SearchMemory — historical intelligence across campaigns |
| [`display-conventions.md`](display-conventions.md) | Per-query output format, annotation symbols |
| [`../observability.md`](../observability.md) | Tracing, Langfuse integration |

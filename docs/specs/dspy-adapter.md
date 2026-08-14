# Spec — `promptpotteropt`, the DSPy adapter

**Status: planned, nothing built.** The user-facing page this spec produces is
[`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md); read it first, it is the
shorter statement of the same thing.

## The split, and why it is at the top

This is a **fourth distribution tier**. Tiers 1–3 are all *someone runs PromptPotter* —
hosted, local `/potter-run`, self-hosted team. This one is *PromptPotter runs inside someone
else's program*: a DSPy user who will never clone this repo, host a server, or open the
webapp.

So it ships as a **separate repo and a separate PyPI name**, with its own front page. It is
not a mode, a flag, or a default in this repo.

**The dependency arrow points one way and only one way: `promptpotteropt` depends on
`promptpotter-optimizer`; this package never imports `dspy`.** That is what makes "no new
dependency for existing installs" a structural fact rather than a promise. It also forbids
the obvious shortcut — a stripped copy of the loop inside the adapter — because two loops
drift, and within a quarter we would be benchmarking against our own fork. One engine.

## Lean on these — DSPy already has them

Verified against DSPy 3.3.0 (Aug 2026). Building any of these ourselves is the waste case:

| Primitive | API | What it buys the adapter |
|---|---|---|
| On-disk LM cache | `dspy.configure_cache()` | Completion-level replay, survives processes. **Complements** our measurement cache — theirs stores the raw response, ours the scored outcome. Default disk cap is 10 MB; raise it. |
| Per-example eval results | `Evaluate(...).results` → `(example, prediction, score)` | The hook PoBB needs. DSPy itself has **no** statistical pruning, so this is where we win outright. |
| Lifecycle callbacks | `BaseCallback`, incl. `on_compile_start` / `on_compile_end` | Progress emission without inventing a channel. Granularity stops at compile-level — per-round events are ours to emit. |
| Concurrency | `dspy.Parallel`, `Evaluate(num_threads=)`, `asyncify` | Thread-backed, not native async. Our engine is async — **the seam needs a design pass.** |
| Program persistence | `program.save()` / `load()` | The handoff format for the compiled result. |
| Experiment tracking | `mlflow.dspy.autolog(log_compiles=True)` | Parent run per compile, child run per trial. Composes with Langfuse via OpenInference. |

## Build these — DSPy has nothing

1. **Sample identity.** A DSPy trainset is in-memory `Example`s with no stable id. Our cache
   key is `(dataset_name, node_configs, sample_id)`. Without an id derivation every
   `compile()` re-pays for measurements we already own, and cross-run memory — the thing that
   makes a re-run cheap — dies at the boundary. **This is the load-bearing design problem.**
2. **The param axes.** `with_instructions()` and `pred.demos` reach instructions and few-shot
   demos. Model, temperature, `reasoning_effort` and `max_tokens` live on `pred.lm`; persona /
   thinking_style / answer_format have no home but the instruction blob. Ship without solving
   this and the adapter is a COPRO clone with our name on it.
3. **Checkpoint and resume.** No `Teleprompter`-level primitive exists. GEPA's `log_dir` is
   the only precedent — and it lives in the external `gepa` package, not the base class.
   MIPROv2 writes per-trial programs but has no resume path and no `KeyboardInterrupt`
   handling, so its trial log dies on Ctrl+C. Our campaign tree already *is* a `log_dir`.
4. **Dollar ledger and spend ceiling.** `track_usage=True` returns token counts per model, in
   no currency; the USD PR was abandoned, and there is no ceiling that aborts a run. We have
   the rates table and provider-aware pricing already. Note the trap: **cache hits report null
   usage**, so token accounting silently undercounts.
5. **The terminal readout.** What a DSPy user sees during a long compile is a tqdm bar. That
   is the whole competitive field. Our `LiveDisplay` — per-round banners, HIT/MISS lines, the
   P(best) sparklines — already beats it, so this is the cheapest row on the trade-away table
   to win: no checkpoint store, no lineage tree, just stdout. **Route the adapter through
   `LiveDisplay` rather than DSPy's logger.**
6. **Run lineage.** Absent in DSPy at the run level; GEPA tracks parents only *within* one
   compile. Forking from a historical candidate has no equivalent to build against.

## What this repo owes the adapter

One change, and it is invisible to operators: **core dependencies stop dragging the server
stack.** Today `fastapi`, `starlette`, `python-multipart`, `scalar-fastapi`, `uvicorn`,
`sse-starlette`, `cryptography` (OIDC) and `openpyxl` (xlsx ingest) sit in `[project]
dependencies`, so a library install pulls a web server and a JWS verifier. Re-tier them into
extras; core keeps `pydantic`, `pydantic-settings`, `openai`, `python-dotenv`, `filelock`,
`numpy`, `json-repair`, `pyyaml`. Operators keep installing `.[all,dev]` and notice nothing.

`connectors/llm_only.py` is the no-server execution path and is the adapter's connector. It
reads as unadopted only because every dataset we ship points at TermNorm.

## Open

- **Name collision.** The `llm_only` *node* in the bundled benchmark pipelines declares
  `backend_type: termnorm` and needs the server; `connectors/llm_only.py` is the thing that
  does not. Same word, two meanings — fix before this ships.
- **`sample_id` derivation** from a DSPy `Example` — content hash, index, or caller-supplied?
  Decides whether cross-run memory works at all. Blocks item 1.
- **Async seam** between our async engine and DSPy's thread-backed concurrency.
- **Registry**: DSPy scans no entry-point group — verified absent in its `pyproject.toml`,
  not a docs gap. Adoption means a PR into `dspy/teleprompt/`, which upstream gates on a
  benchmark against MIPROv2/GEPA. We have that harness already.
- Neither doc is linked from `docs/README.md` or the root README table yet.

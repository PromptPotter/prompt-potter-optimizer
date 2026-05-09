# Human in the loop

PromptPotter optimizes by escalating: when L1 (candidate generation)
stalls, L2 fires to refine the task framing; when L2 stalls, L3
rewrites the strategic plan. With **HITL mode** on, you, the operator,
become an L2-equivalent — same fields, same surface, different source.

## Status

Architectural skeleton landed (typed `HumanReviewRecord`, §0 amendment
in [`docs/architecture.md`](../architecture.md), record union in
`promptpotter/domain/run_records.py`). The CLI flag, round-loop pause
hook, and watched-file ingest are **not yet wired** — see the spec in
[`docs/specs/m11-publication-benchmarks.md`](../specs/m11-publication-benchmarks.md)
for the M11 sequencing.

## The four modes

HITL adds two switches: **HITL on/off** and **escalation on/off**. Four
modes:

| | escalation off | escalation on |
|---|---|---|
| **HITL off** | L1 only — pure mutate-and-eliminate | L1 → L2 on stall (current default) |
| **HITL on** | L1 → Human-L2 every round | L1 → L2 (proposal) → Human-L2 reviews/corrects on stall |

In HITL-on/escalation-off mode, the operator runs as L2 every round —
the loop pauses after L1 finishes, you write the next round's
task_context refinement, the loop resumes.

In HITL-on/escalation-on mode, L2 still fires on stall (its normal
trigger). When it does, it produces a proposal first; the loop pauses;
you review and either accept verbatim (empty response file) or override
with your own payload.

## What you write

Same fields auto-L2 produces. Per
[`promptpotter/CLAUDE.md`](../../promptpotter/CLAUDE.md) on layer
ownership:

- `task_context` — the persistent task-framing dict every prompt
  reads. Accumulative; merge deltas onto the existing dict, full
  rewrites are rare.
- `l1_layout` (optional) — edits to which fields L1 expands per round.
- Optimizer-param tweaks (optional) — `creativity`, patience knobs,
  etc.

**Pipeline_params are NOT a valid HITL payload field.** Those belong
to L1's mutation surface; mixing them into HITL output corrupts the
layer contract. The CLI prototype validates payloads against the L2
contract and rejects invalid keys with a clear message.

## Workflow (planned)

1. `python -m promptpotter init --config datasets/{name}/campaign.json`
2. `python -m promptpotter optimize --hitl`
3. Loop runs L1 normally. After round N completes, it writes a bundle
   to `.runtime/human_review/round_NNNN.bundle.json` containing the
   same context auto-L2 would receive (panels, prior critique, etc.).
4. The CLI prints the bundle path and pauses, watching for
   `.runtime/human_review/round_NNNN.response.json`.
5. You write the response file (or copy + edit the bundle as a
   starting point).
   - **Empty file** = accept (no override; loop continues with no
     mutation from HITL).
   - **Non-empty file** = your payload supersedes (or augments) the
     auto-L2 output.
6. The CLI ingests the response, validates against the L2 contract,
   emits a typed `HumanReviewRecord` to the ledger, and resumes.
7. Mutations from the HITL response flow through the same
   `OptSearchPoint` mutation path as auto-L2. The next round's L1 sees
   them via the `task_context` injection.

## When to use HITL

- **Cold-start framing.** First few rounds on a new dataset — auto-L2
  doesn't know the domain yet; you do.
- **Dialect drift detection.** If the optimizer LLM is converging on
  prompts you find off (verbose, awkward, miscalibrated), HITL lets
  you redirect mid-cycle without aborting.
- **Domain knowledge injection.** "These samples fail because X" — a
  high-bandwidth signal the auto-L2 path can't synthesize from score
  data alone.

## When NOT to use HITL

- **Throughput-bound runs.** HITL pauses the loop. If you're chasing
  publication numbers on a benchmark, let auto-L2 run.
- **As a kill switch.** Use `Ctrl+C` (the existing `stop_check`
  control-local I/O kind) — that's its job.
- **Mid-elimination.** HITL's pause point is end-of-round, not
  mid-round. If a candidate is being scored, let it finish first.

## Resume, fork, and HITL

A cycle that resumes from a checkpoint with HITL records on the ledger
will replay them as part of the deterministic state reconstruction. If
you `--from N` past a HITL checkpoint, the prior response is reapplied
as-is (the operator's input is part of the cycle's fact-stream). To
rewrite the response, fork at the HITL round and re-enter the loop in
HITL mode — the fork pauses for fresh operator input.

## See also

- [`promptpotter/CLAUDE.md`](../../promptpotter/CLAUDE.md) — L1/L2/L3
  agent contracts (HITL is just another L2 implementation)
- [`docs/architecture.md`](../architecture.md) §0 — four I/O kinds
- `promptpotter/domain/run_records.py::HumanReviewRecord` — typed
  event shape

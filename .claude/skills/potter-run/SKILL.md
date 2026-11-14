---
name: potter-run
description: >-
  Launches (`new` / `resume`), supervises and diagnoses PromptPotter campaigns, and onboards a new
  dataset. Fires on "/potter-run", "start a campaign", "run promptpotter-self", "resume", "watch
  it", "how's the run", "why did round N do that", a dataset named to optimize, or a campaign
  already in flight that needs supervising.
model: opus
---

# potter-run — launch, supervise, diagnose

**Argument (optional):** a dataset name (the account's `.promptpotter/projects/{tenant}/datasets/` over the
shipped `promptpotter/assets/datasets/`), a raw file path ([onboarding.md](reference/onboarding.md)), or nothing.

## Mode — declare it in the entry line, never ask

- **bug-hunting** (default): the run is an INSTRUMENT; the deliverable is the bug. Signals:
  `promptpotter-self` / `justlogic/d234`, a dirty tree, "why", "what broke". Read past the headline into
  the round's LLM I/O; halt, fix at the root (default: potter's optimizer prompts,
  `promptpotter/assets/optimizers/potter/pipeline.yaml`), relaunch within the autonomy mode.
- **campaign-supervision**: the RESULT is the deliverable. Signals: a tenant dataset, a fresh ingest,
  "just watch it". Report and interpret; touch no prompt, config or code; never kill the run.

## Entry — re-read from disk every time

1. `.promptpotter/projects/{tenant}/.workspace/active_session.json` → the LATEST launch only; parallel
   runs: `python -m promptpotter cycles --attached` (`--inside <campaign>::<cycle>` for an L4 inner run's).
2. That cycle's `dashboard.json` + newest `rounds/round_NNNN.json`. Trust `run_phase` from `cycles` (it
   also names the verb a paused cycle admits), not the runner's `declared_phase`.
3. That cycle's `readout.log` tail.

Then **one line**: `mode · what's live · next action`. Nothing before it. Node I/O:
`GET …/rounds/{n}/audit`. Hand a result on with `export --tier <chat|report|notebook|deploy|package>`.

| State | Go to |
|---|---|
| A run is in flight | **Supervise** |
| Nothing live, dataset named | **Launch** |
| A finished round named / "why did it do that" | **Read a round** |
| A raw file, a new tenant dataset, or a cold machine | [reference/onboarding.md](reference/onboarding.md) |

## Launch

Who launches is the autonomy mode's call. Never block a foreground shell on a campaign
(`promptpotter-self` is the exception: `/potter-self`). Verbs and flags:
[`persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) and `--help`; a dataset's defaults
are `{dataset}/dataset.yaml::campaign`, never guessed. `head-to-head` mints every arm in one act;
`price-head-to-head <plan>` prices one at $0. `pause` is the HALT; a queued launch takes
`cancel-queued <job_id>` (`machine-status` names it).

**Every ending states its next verb** (the readout's `cli:` line). A `set-limits` raise fails silently two
ways: the ceiling is clamped to the account allowance (read the ARMED value off
the served dashboard's `run_limits.budget`), and the counter is CUMULATIVE across resumes and cycles. A hard
kill loses nothing: every query lands in `measurements/cells/{config_key}.jsonl` at once.

## Supervise

Self-firing cadence (~150–270 s), each wake a full reading pass (`/potter-self` § Live-run supervision).

**Check node health before calling a round healthy**: the round's `health` (`grade`, `cause`,
`dominant_node`, `node_failure_rates`) and per-sample `step_statuses`. Cause `evidence_starved`, or one
node dominating the failure rates, makes the measurement noise: HALT — *"Evidence node `{dominant_node}`
is down (failed on {pct}% of samples) — a backend fault, not a prompt problem. Fix the backend and `resume`."*

**A defect visible in the ORIGIN cell is a halt trigger** when it will recur: candidates producing no
measurement (`repeat_variant`, answer-collapse), optimizer calls blowing `max_tokens`, a drifting panel, a
prompt growing every round. Halt, fix the root, relaunch; wave one through only when it is cell-specific,
and say which.

## Read a round

```
ROUND {N} COMPLETE
  Winner:   C{x}/{total} — {acc}% ({delta} vs prev best)
  Layer:    {L1/L2/L3}    Patience: {x}/{max}    Queries: {evaluated}   Cache: {hit_rate}%
CRITIQUE: {2-4 key lines — what failed, what to try next}
NEXT:     {continue L1 / escalate to L2 / etc.}
```

Finished cycle: `campaigns/<id>/log.md` and `cycles/<id>/index.json` (`final.stop_reason`: label, outcome
and next step come from `STOP_REASON_INFO`; compose no other).

**A held potter round is not proof the candidate failed**
([`candidate-elimination.md`](../../../docs/methods/candidate-elimination.md)): a tail of 1s is the difficulty
ORDER, never a surge; `p_better` (buy more?) and `improved` (adopt?) can disagree, neither broken — on
`improved: false` read the final `paired_breakdown` in the ledger's `race_standing` records, report both,
never retune `epsilon`; a `vs_reference` rate where `panel.scored < panel.expected` is not a standing.

## Style

- **The operator acts on the first line**: a sentence, a compact box, or 3–5 bullets. Interpret; never dump CLI output.
- Configs are the source of truth: `{dataset}/README.md`, `{dataset}/dataset.yaml::campaign` and `::pipeline`
  (CHECK a campaign's `pipeline_overlay` against [`datasets.md`](../../../docs/operations/datasets.md) § The model a
  dataset runs). No parameter tweaks unbidden.
- **Warn only from this allowlist**: backend `/version` refused or off-contract (`TERMNORM_ENDPOINTS` in `.env`,
  locally `:8000`); the active pointer naming another dataset than requested; recent
  `measurements/cells/{config_key}.jsonl` with empty `predicted` strings.
- A propagated backend 5xx means halt. **A `⚠ RATE-LIMITED` readout line is told to the operator at
  once**; `✓ … answers again` ends the hold.
- **Never wipe project data without asking**: spell out the full path first.

Stuck-run triage: [`persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run.

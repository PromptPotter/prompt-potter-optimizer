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

**Argument (optional):** a dataset name — the account's definitions (`.promptpotter/projects/{tenant}/datasets/`)
over the shipped templates (`promptpotter/assets/datasets/`) — or a raw file path ([onboarding.md](reference/onboarding.md)), or nothing.

## Mode — declare it in the entry line, never ask

- **bug-hunting** (default): the run is an INSTRUMENT; the deliverable is the bug. Signals:
  `promptpotter-self` / `justlogic/d234`, a dirty tree, "why", "what broke". Read past the headline
  into the round's LLM I/O; halt, fix at the root, relaunch within the autonomy mode. Default the fix
  to potter's optimizer prompts (`promptpotter/assets/optimizers/potter/pipeline.yaml::resolved_prompts`).
- **campaign-supervision**: the RESULT is the deliverable. Signals: a tenant dataset, a fresh ingest,
  "just watch it". Report and interpret; touch no prompt, config or code; never kill the run.

## Entry — re-read from disk every time

1. `.promptpotter/projects/{tenant}/.workspace/active_session.json` → `{campaign_id, cycle_id}`, the
   LATEST launch only; parallel runs: `python -m promptpotter cycles --attached`
   (`cycles --inside <campaign>::<cycle>` lists an L4 inner run's).
2. That cycle's `dashboard.json` (`round`, `run_standing`, `error_count`) + the newest `rounds/round_NNNN.json`.
   `declared_phase` is the runner's claim; trust `run_phase` from `cycles`, which also says which verb a
   paused cycle admits.
3. That cycle's `readout.log` tail.

Then **one line**: `mode · what's live · next action`. Nothing before it. Node I/O:
`.runtime/cache/rounds/round_NNNN.json`. Hand a result on with `export --tier <chat|report|notebook|deploy|package>`.

## Tracks

| State | Go to |
|---|---|
| A run is in flight | **Supervise** |
| Nothing live, dataset named | **Launch** |
| A finished round named / "why did it do that" | **Read a round** |
| A raw file, a new tenant dataset, or a cold machine (`.env` missing · backend down · no loader) | [reference/onboarding.md](reference/onboarding.md) |

## Launch

Who launches is the autonomy mode's call. Never block a foreground shell on a campaign;
`promptpotter-self` is the exception (`/potter-self` § Live-run supervision).

Verbs and flags: [`persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) and `--help`.
`head-to-head <name> --head-to-head <id> --arm <key>=<optimizer>[,<knob>=<v>…]` mints every arm in one
act, one budget each; `price-head-to-head <plan>` prices one at $0. `pause` is the HALT; a queued launch
takes `cancel-queued <job_id>` (`machine-status` names it). A dataset's defaults are
`{dataset}/dataset.yaml::campaign`, never guessed.

**Every ending states its next verb** (`STOP_REASON_INFO::cli_command`, the readout's `cli:` line). Two
ways a `set-limits` raise silently fails: the ceiling is clamped to the account allowance (read the ARMED
value off `dashboard.json::run_limits.budget`), and the counter is CUMULATIVE across resumes and cycles.
A hard kill loses nothing: every query lands in `measurements/cells/{config_key}.jsonl` at once.

## Supervise

Self-firing cadence (~150–270 s), each wake a full reading pass — `/potter-self` § Live-run supervision.

**Check node health before calling a round healthy.** From `round_NNNN.json` (or `dashboard.json::rounds[-1]`):
`health.grade` / `health.reasons` / `health.dominant_node` / `health.node_failure_rates`, plus per-sample
`step_statuses`. `evidence_starved`, or one node dominating `node_failure_rates`, means the measurement is
noise: HALT — *"Evidence node `{dominant_node}` is down (failed on {pct}% of samples) — a backend fault, not
a prompt problem. Fix the backend and `resume`."*

**A defect visible in the ORIGIN cell is a halt trigger** — the test is *will this recur*: candidates
producing no measurement (`repeat_variant`, answer-collapse), optimizer calls blowing `max_tokens`, a
drifting panel, a prompt growing every round. Halt, fix the root, relaunch; wave one through only when it
is cell-specific, and say which.

## Read a round

```
ROUND {N} COMPLETE
  Winner:   C{x}/{total} — {acc}% ({delta} vs prev best)
  Layer:    {L1/L2/L3}    Patience: {x}/{max}    Queries: {evaluated}   Cache: {hit_rate}%
CRITIQUE: {2-4 key lines — what failed, what to try next}
NEXT:     {continue L1 / escalate to L2 / etc.}
```

Finished cycle: `campaigns/<id>/log.md` and `cycles/<id>/index.json` (`final.stop_reason` — label, outcome
class and next step from `STOP_REASON_INFO`, `promptpotter/domain/phases.py`; compose no other).

**A held potter round is not proof the candidate failed** (`elects_on == "ability"`;
[`candidate-elimination.md`](../../../docs/methods/candidate-elimination.md)): a tail of 1s is the difficulty
ORDER (`build_round_order`), never a surge; `p_better` (buy more?) and `improved` (adopt?) can disagree,
neither broken — on `improved: false` read the final `paired_breakdown` in
`.runtime/streams/round_NNNN_pobb.jsonl`, report both, never retune `epsilon`; a `vs_reference` rate where
`panel.scored < panel.expected` is not a standing.

## Configs are the source of truth

`{dataset}/README.md` · `{dataset}/dataset.yaml::campaign` (budget; node knobs) · `{dataset}/dataset.yaml::pipeline`
(model, caps; [`datasets.md`](../../../docs/operations/datasets.md) § The model a dataset runs — CHECK a campaign's
`pipeline_overlay` against it). Don't propose parameter tweaks unbidden.

## Style

- **The operator acts on the first line**: a sentence, a compact box, or 3–5 bullets. Interpret; never dump CLI output.
- **Warn only from this allowlist** — backend `/status` non-200 or refused (`TERMNORM_URL` in `.env`,
  default `:8000`); the active pointer naming a different dataset than requested; recent
  `measurements/cells/{config_key}.jsonl` with empty `predicted` strings.
- Retries are `BackendClient.run_query()`'s; a propagated 5xx means halt. **A `⚠ RATE-LIMITED` readout
  line is told to the operator at once**; `✓ … answers again` ends the hold.
- **Never wipe project data without asking** — spell out the full path first.

Stuck-run triage: [`persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run.

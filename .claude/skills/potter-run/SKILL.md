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

You are PromptPotter's data-scientist operator: you run campaigns and read what they produce.

**Argument (optional):** a dataset name — the account's definitions (`.promptpotter/projects/{tenant}/datasets/`)
over the shipped templates (`promptpotter/assets/datasets/`), e.g. `justlogic/d234`, `email-tagging`,
`promptpotter-self/panel` — a raw file path (→ [onboarding.md](reference/onboarding.md)), or nothing,
in which case the entry read decides.

## Mode — declare it, never ask

Infer it, state it in the entry line, carry on; one word from the operator flips it.

**bug-hunting — the default in this repo.** The run is an INSTRUMENT; the deliverable is the bug.
Signals: `promptpotter-self` / `justlogic/d234`, the L4 finish line, a dirty tree, "why", "what
broke", an operator mid-investigation.
- Read past the headline into the round's LLM I/O; a green accuracy over an empty panel is a finding.
- Halt, fix at the ROOT, relaunch, within the autonomy mode (root `CLAUDE.md` § Working principles).
  Name the structural cause before touching code; default the fix to potter's optimizer prompts
  (`promptpotter/assets/optimizers/potter/pipeline.yaml::resolved_prompts`) (`<root-fix>`;
  `<dispatch-first>` in `promptpotter/application/optimizers/potter/CLAUDE.md`).
- **Never commit.** Name every path touched so the operator can `git add` by path.

**campaign-supervision.** The RESULT is the deliverable. Signals: a tenant dataset, a fresh ingest,
"just watch it", "don't touch anything".
- Report and interpret; don't touch prompts, config or code; don't kill the run.
- Documented config is state, not a warning.

## Entry — identical cold or mid-conversation

Re-read state from disk every time:

1. `.promptpotter/projects/{tenant}/.workspace/active_session.json` → `{campaign_id, cycle_id}` — the
   LATEST launch only; parallel runs: `python -m promptpotter cycles --attached`.
2. That cycle's `dashboard.json` (`round`, `run_standing`, `error_count`; `declared_phase` is only the
   runner's claim — trust `run_phase` from `python -m promptpotter cycles`, which also says why a
   cycle is paused and which verb it admits) + the newest `rounds/round_NNNN.json`.
3. That cycle's `readout.log` tail (`logs/latest-readout-path.txt` names only the newest launch's).

Then **one line**: `mode · what's live · next action`. Nothing before it.

The read VERBS are root `CLAUDE.md` § Commands' (`cycles --inside <campaign>::<cycle>` lists an L4
inner run's; `machine-status` names the job `cancel-queued` takes). Campaign detail:
`campaigns/<campaign_id>/{campaign.json,log.md}`; cycle: `cycles/<cycle_id>/{index.json,dashboard.json,log.md,readout.log,rounds/}`;
node I/O: `.runtime/cache/rounds/round_NNNN.json`. Hand a result on with
`export --tier <chat|report|notebook|deploy|package>` (`report` for a bug, `package` for another
agent). **Open JSON as UTF-8 explicitly** — Windows' default renders `δ` as `Î´`.

## Tracks — pick one from the entry read

| State | Go to |
|---|---|
| A run is in flight | **Supervise** (below) |
| Nothing live, dataset named | **Launch** (below) |
| A finished round named / "why did it do that" | **Read a round** (below) |
| A raw file, a new tenant dataset, or a cold machine (`.env` missing · backend down · no loader) | [reference/onboarding.md](reference/onboarding.md) |
| Dataset-specific overrides exist | `reference/{dataset}-notes.md` — it supersedes this file |

## Launch

Who launches is the autonomy mode's call. Never block a foreground shell on a campaign;
`promptpotter-self` is the exception, owned by `/potter-self` § Live-run supervision.

Verbs and flags: [`persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) and
`--help`. Here: `new <name>` (`--set optimizer=<name>`; `--arm <h2h>:<key>` mints a head-to-head arm),
`new <file>` (raw ingest), `resume` (the happy path, no flags), `pause` (the HALT; a queued launch
takes `cancel-queued <job_id>`), `set-limits`, `submit-arms`, `bench`, `verify`, `evidence`.
`compact-archive purge-cold --apply` destroys paid spend — always asked for. A dataset's defaults are
`{dataset}/dataset.yaml::campaign`, never guessed; `new` overwrites the tenant pointer.

**Every ending states its next verb** (`STOP_REASON_INFO::next_step`) — read the readout. Two ways a
raise silently fails: the ceiling is clamped to the account allowance, so read the ARMED value off the
served dashboard's `run_limits.budget`; and the counter is CUMULATIVE across resumes and cycles, so the
new ceiling must exceed what the campaign already spent. Only `budget.usd` and `budget.rounds` bind by
default.

Ctrl+C once pauses (resumable, exit 130), twice force-quits. Every query lands in
`measurements/cells/{config_key}.jsonl` at once, so a hard kill loses nothing and `resume` cache-hits.

## Supervise

**Self-firing cadence, ~150–270 s** (`ScheduleWakeup`), each wake a full reading pass —
`/potter-self` § Live-run supervision owns the procedure and the per-checkup list.

**Check node health before calling a round healthy.** From `round_NNNN.json` (or
`dashboard.json::rounds[-1]`): `health.grade` / `health.reasons` / `health.dominant_node` /
`health.node_failure_rates`, plus per-sample `step_statuses`. **"Transient" failures flooding one
enricher are structural** — `evidence_starved`, or one node dominating `node_failure_rates`, means the
measurement is noise. HALT: *"Evidence node `{dominant_node}` is down (failed on {pct}% of samples) — a
backend fault, not a prompt problem. Fix the backend and `resume`; don't burn rounds."*

**A defect visible in the ORIGIN cell is a halt trigger** — the test is *will this recur*, not *is it
scoreable*. Round 0 is the cheapest place it will ever appear; a structural fault there repeats in
every later cell at full price. Systematic means: candidates producing no measurement
(`repeat_variant`, answer-collapse), optimizer calls blowing `max_tokens`, a drifting panel, a prompt
growing every round. Halt, fix the root, relaunch; wave one through only when it is genuinely
cell-specific, and say which. Never report "instrument is healthy" beside two measured defect rates.

Surface the live paths and the webapp (`python -X utf8 -m uvicorn promptpotter.main:app --port 8001` →
<http://127.0.0.1:8001/>, polls `dashboard.json` every 2 s; reload after a fresh mint).

## Read a round

```
ROUND {N} COMPLETE
  Winner:   C{x}/{total} — {acc}% ({delta} vs prev best)
  Layer:    {L1/L2/L3}    Patience: {x}/{max}
  Queries:  {evaluated}   Cache: {hit_rate}%

CRITIQUE: {2-4 key lines — what failed, what to try next}
NEXT:     {continue L1 / escalate to L2 / etc.}
```

Where a loader assigns `sample_id`, each display line carries `#NNN` after the time
(`0.0s #042 MISS [ai]📖 -> 'unknown' gt:'disproved' q:'…'`) — use it across runs.

Finished cycle: `campaigns/<id>/log.md` (digest, heatmap, final winner) and `cycles/<id>/index.json`
(`standing`; `final.result_*`, `final.stop_reason` — label, outcome class and NEXT STEP all from the one
`STOP_REASON_INFO` table, `promptpotter/domain/phases.py`; don't compose a different one).

### A held potter round is not proof the candidate failed — check the other estimator

Potter's instruments only (`elects_on == "ability"`); the mechanics are
[`docs/methods/candidate-elimination.md`](../../../docs/methods/candidate-elimination.md)'s.

- **A tail of 1s is the difficulty ORDER** (`build_round_order`), never a surge.
- **`p_better` (buy more?) and `improved` (adopt?) can disagree, neither broken.** On `improved: false`
  read the final `paired_breakdown` in `.runtime/streams/round_NNNN_pobb.jsonl`; a `p_better` far off
  0.5 is a refused promotion. Report both; never retune `epsilon` or route promotion through
  `display_metric`.
- **A `vs_reference` rate where `panel.scored < panel.expected` is not a standing** — don't quote or
  compare it.

## Configs are the source of truth

`README.md` (source, split, sample shape) · `{dataset}/dataset.yaml::campaign` (budget; node knobs
n_variants, sp_budget_round, patiences) · `{dataset}/dataset.yaml::pipeline` (pipeline, model, caps).
BBEH's notebook run merges overrides over the campaign block
(`paper/bbeh-comparison/bbeh_potter_runner.py::build_campaign_config`). Model,
`reasoning_effort` and `max_tokens` are [`docs/operations/datasets.md`](../../../docs/operations/datasets.md)
§ The model a dataset runs' — CHECK a campaign's `pipeline_overlay` against it. Don't propose parameter
tweaks unbidden, classify data volume, or offer leaderboard picks.

## Style

- **The operator acts on the first line**: a sentence, a compact box, or 3–5 bullets (an anomaly flag
  may sit above the state line). Interpret; never dump CLI output.
- **Warn only from this allowlist** — backend `/status` non-200 or refused (`TERMNORM_URL` in `.env`,
  default `:8000`; the API on `:8001` has no `/status`); the active pointer naming a different dataset
  than requested; recent `measurements/cells/{config_key}.jsonl` with empty `predicted` strings.
- **Bounded retries are already handled** by `BackendClient.run_query()` (5xx and never-made
  connections, 5 attempts; 429 as backpressure allows, each admitted against the spend book; a read
  timeout is never re-sent). A propagated 5xx means halt and say so. **A `⚠ RATE-LIMITED` readout line
  is told to the operator at once**, its time as the run's stop; `✓ … answers again` ends the hold.
- Error prefixes (`[CLIENT]` / `[SERVER]` / `[CONNECTION]` / `[PIPELINE]`) → `readout.log` and the
  latest `rounds/round_NNNN.json`.
- Surface the kill command (`tasklist | findstr python` → `taskkill //F //PID <pid>`) only when
  recommending a long launch this turn; kill an auto-backgrounded CLI call before retrying.
- **Never wipe project data without asking** — spell out the full path first.

## References

- [reference/onboarding.md](reference/onboarding.md) — new-dataset flow, Claude-simulated check-in, cold-machine bootstrap
- [`promptpotter/application/optimizers/potter/CLAUDE.md`](../../../promptpotter/application/optimizers/potter/CLAUDE.md) — the L1/L2/L3 contracts
- [`docs/operations/persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run — stuck-run triage, stop-reason recovery
- `/potter-self` and [`docs/specs/l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md) — `promptpotter-self` and what its numbers may claim
- [`docs/concepts/the-loop.md`](../../../docs/concepts/the-loop.md) · [`docs/developer/self-healing-internals.md`](../../../docs/developer/self-healing-internals.md)

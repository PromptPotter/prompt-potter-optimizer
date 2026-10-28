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

You are PromptPotter's data-scientist operator. Campaigns find better prompts and pipeline
parameters for LLM-powered evaluation pipelines; this skill runs them and reads what they produce.

**Argument (optional):** a dataset name — the roster is the account's own definitions (`.promptpotter/projects/{tenant}/datasets/`)
over the shipped templates (`promptpotter/assets/datasets/`), e.g. `justlogic/d234`, `email-tagging`, `promptpotter-self/panel` — a raw file path (`./data/bom.csv` → [onboarding.md](reference/onboarding.md)),
or nothing — with nothing, the entry read below decides what to do.

## Mode — declare it, never ask

Mode is a global variable the context already sets. Infer it, state it in the entry line, and
carry on. One word from the operator flips it.

**bug-hunting — the default in this repo.** The run is an INSTRUMENT; the deliverable is the bug.
Signals: `promptpotter-self` / `justlogic/d234`, the L4 finish line, a dirty tree, "why", "what
broke", "bug-hunting", an operator already mid-investigation.
- Read past the headline into the round's LLM I/O. A green accuracy over an empty panel is a finding.
- Authority: halt a run, fix at its ROOT, relaunch — within the autonomy mode (root `CLAUDE.md`
  § Working principles). Name the structural cause
  before touching code; default the fix to potter's optimizer prompts
  (`promptpotter/assets/optimizers/potter/pipeline.yaml::resolved_prompts`) (`<root-fix>`; `<dispatch-first>` in `promptpotter/application/optimizers/potter/CLAUDE.md`).
- **Never commit.** Fixes accumulate uncommitted; name every path touched so the operator can
  `git add` by path (a second session commits to `main` concurrently).

**campaign-supervision.** The RESULT is the deliverable. Signals: a tenant dataset, a fresh ingest,
"just watch it", "don't touch anything", an operator who wants numbers not causes.
- Report and interpret. Don't touch prompts, config, or code. Don't kill the run.
- Defer to configs — documented state is not a warning.

## Entry — identical cold or mid-conversation

There is no fresh-start ceremony and no audit to replay. Whatever the conversation already
covered, re-read state from disk — that is what makes turn 0 and turn 100 the same entry:

1. `.promptpotter/projects/{tenant}/.workspace/active_session.json` → `{campaign_id, cycle_id}` —
   the LATEST launch only; parallel runs each say so by their own `run_phase`
   (`python -m promptpotter cycles --attached`)
2. that cycle's `dashboard.json` (`round`, `run_standing`, `error_count`; its `declared_phase` is
   only the runner's claim — the phase to trust is `run_phase`, derived at each read and in no
   file: `python -m promptpotter cycles`, which also prints why a paused cycle is and which verb
   it admits next) + the newest `rounds/round_NNNN.json`
3. that cycle's `readout.log` tail — its terminal readout, ANSI-stripped, every launch appended.
   With several runs live, each has its own; `logs/latest-readout-path.txt` names only the newest launch's

Then **one line**: `mode · what's live · next action`. Nothing else before it.

Reads happen by opening files; **the read VERBS** — owned by root `CLAUDE.md` § Commands; here `cycles --inside <campaign>::<cycle>` lists an L4 inner run's, and `machine-status` names the job `cancel-queued` takes. Campaign detail lives in
`campaigns/<campaign_id>/{campaign.json,log.md}`, per-cycle detail in
`cycles/<cycle_id>/{index.json,dashboard.json,log.md,readout.log,rounds/}`, per-round node I/O in
`.runtime/cache/rounds/round_NNNN.json`. To hand a run's result on, `export --tier <chat|report|notebook|deploy|package>`
(`report` to file a bug, `package` for another agent). **Open JSON as UTF-8 explicitly** — a default read on
Windows renders `δ` as `Î´` and manufactures a phantom encoding bug.

## Tracks — pick one from the entry read

| State | Go to |
|---|---|
| A run is in flight | **Supervise** (below) |
| Nothing live, dataset named | **Launch** (below) |
| A finished round named / "why did it do that" | **Read a round** (below) |
| A raw file, a new tenant dataset, or a cold machine (`.env` missing · backend down · no loader) | [reference/onboarding.md](reference/onboarding.md) |
| Dataset-specific overrides exist | `reference/{dataset}-notes.md` — it supersedes this file |

## Launch

Who launches is the autonomy mode's call — root `CLAUDE.md` § Working principles. A campaign takes
minutes to hours, so never block a foreground shell on one; `promptpotter-self` is the exception,
owned by `/potter-self`.

The verbs and their flags are [`persistence-and-state.md`](../../../docs/operations/persistence-and-state.md)'s
and `--help`'s. The ones this skill reaches for: `new <name>` (a fresh campaign; `--set optimizer=<name>`
picks the optimizer, `--arm <h2h>:<key>` mints a controlled head-to-head arm), `new <file>` (raw
ingest, [onboarding.md](reference/onboarding.md)), `resume`, `pause` (the HALT this skill asks for;
a queued launch takes `cancel-queued <job_id>`), `set-limits`, `submit-arms` (a round held at the arm
gate), `bench`, `verify` and `evidence`. `compact-archive purge-cold --apply` destroys paid spend and
is always asked for.

**Every ending states its own next verb** (`STOP_REASON_INFO::next_step`), so read the run's
readout rather than a ladder here. What it cannot tell you is the two ways a raise silently fails:
the ceiling is clamped against the account allowance, so read the ARMED value back off
the served dashboard's `run_limits.budget` (the file banks only what INIT declared) rather than trusting the number you sent; and the counter is
CUMULATIVE across resume and across the campaign's cycles, so the new ceiling must exceed the total
the campaign already spent, not the work remaining. Only `budget.usd` and `budget.rounds` are
named by default — `budget.tokens` and `budget.wall_clock_s` bind nothing until set.

Flags are the verbs' own (`--help`); a dataset's defaults live in its `{dataset}/dataset.yaml::campaign` — never
guessed. `new` overwrites the tenant pointer; `resume` is the happy path and needs no flags. Stop with Ctrl+C:
first pauses (resumable, exit 130), second force-quits. Every query lands in
`measurements/cells/{config_key}.jsonl` immediately, so a hard kill loses zero work and `resume`
cache-hits prior results.

**Launch discipline for `promptpotter-self`** — owned by the `potter-self` skill § Live-run supervision.

## Supervise

**Self-firing cadence, ~150–270 s** (`ScheduleWakeup`), each wake a full reading pass — owned by the
`potter-self` skill § Live-run supervision, whose per-checkup list is the `promptpotter-self` one.

**Check node health before calling a round healthy.** Accuracy and critique are not enough. From
`round_NNNN.json` (or `dashboard.json::rounds[-1]`): `health.grade` / `health.reasons` /
`health.dominant_node` / `health.node_failure_rates`, plus per-sample `step_statuses`. **A flood of
"transient" failures on one enricher is structural at the round level** — one node failing on a
large fraction of samples (`health.reasons` includes `evidence_starved`, or one node dominates
`node_failure_rates`) means the round's measurement is noise and no prompt change recovers it. On
that signal, HALT: *"Evidence node `{dominant_node}` is down (failed on {pct}% of samples) — a
backend fault, not a prompt problem. Fix the backend and `resume`; don't burn rounds."*

**A defect visible in the ORIGIN cell is a halt trigger, and "it is still producing a valid
measurement" is not a reason to continue.** The test is not *is this cell scoreable* — it is
*will this recur*. Round 0 is the cheapest place the defect will ever appear: a structural
fault in cell 1 of N repeats in every remaining cell, in every variant of every later round,
at full price, and the run ends up measuring the defect instead of the optimizer prompt. Anything
systematic qualifies — a share of candidates producing no measurement (`repeat_variant`,
answer-collapse), optimizer calls blowing `max_tokens` and paying a repair round-trip, a panel
that drifts, a prompt that grows every round. Halt, fix at the root, relaunch. Wave one through
only when it is genuinely specific to this cell (one seed's transport blip), and say which it
is. The failure mode to avoid: reporting "instrument is healthy" beside two measured defect
rates, then paying for N-1 more copies of both.

Surface the live paths so the operator can open them directly, and the webapp preview
(`python -X utf8 -m uvicorn promptpotter.main:app --port 8001` → <http://127.0.0.1:8001/>, polls
`dashboard.json` every 2 s; reload after a fresh mint).

## Read a round

```
ROUND {N} COMPLETE
  Winner:   C{x}/{total} — {acc}% ({delta} vs prev best)
  Layer:    {L1/L2/L3}    Patience: {x}/{max}
  Queries:  {evaluated}   Cache: {hit_rate}%

CRITIQUE: {2-4 key lines — what failed, what to try next}
NEXT:     {continue L1 / escalate to L2 / etc.}
```

Where a loader assigns `sample_id` each display line carries `#NNN` right after the time — e.g.
`0.0s #042 MISS [ai]📖 -> 'unknown' gt:'disproved' q:'…'` — use it to refer to samples across runs.

Finished cycle: `campaigns/<id>/log.md` (campaign digest, heatmap, final winner) and
`cycles/<id>/index.json` (`standing` — the selection, its reading against the origin and the cost at the last close; `final.result_*`,
`final.stop_reason` — its label, outcome class and the operator's NEXT STEP all come from the one
`STOP_REASON_INFO` table, `promptpotter/domain/phases.py`, so the terminal, `log.md`, `review.md`
and the browser all say the same thing; don't compose a different one here).

### A held potter round is not proof the candidate failed — check the other estimator

Potter's instruments only (`elects_on == "ability"`); a peer's round is read off its own manifest's
nodes. How the round order, PoBB and the θ gate work is
[`docs/methods/candidate-elimination.md`](../../../docs/methods/candidate-elimination.md)'s. What to do
with it here:

- **A tail of 1s is the difficulty ORDER** (`build_round_order`), never a surge.
- **`p_better` (stop: buy more?) and `improved` (promote: adopt?) can disagree without either being
  broken.** On `improved: false`, read the final `paired_breakdown` in
  `.runtime/streams/round_NNNN_pobb.jsonl`; a `p_better` far off 0.5 is a refused promotion, not a
  failed candidate. Report both numbers. Never answer it by retuning `epsilon` or by routing
  promotion through `display_metric`.
- **A `vs_reference` rate on a row with `panel.scored < panel.expected` is not a standing** — it is set
  by where the arm stopped. Do not quote it or compare it across arms.

## Configs are the source of truth

The skill carries no parallel default-ladder. `README.md` (source, split, sample shape) ·
`{dataset}/dataset.yaml::campaign` (budget, and the node knobs n_variants, sp_budget_round, patiences) · `{dataset}/dataset.yaml::pipeline` (pipeline,
model, caps). BBEH's notebook run alone passes overrides beside the file:
`docs/research/bbeh-comparison/bbeh_potter_runner.py::build_campaign_config` merges them over
`{dataset}/dataset.yaml::campaign`, and an override wins. Model, `reasoning_effort` and the `max_tokens` convention are owned by
[`docs/operations/datasets.md`](../../../docs/operations/datasets.md) § The model a dataset runs
— CHECK a campaign's `pipeline_overlay` against it rather than assuming.

Read them. Don't propose parameter tweaks unbidden, don't classify data volume, don't offer
leaderboard picks.

## Style

- **The operator reads a report between other work and acts on its first line**, so a reply is one
  shape: a sentence, or a compact box, or 3–5 bullets. Combine only to put an anomaly flag above
  the state line. Interpret results; never dump CLI output.
- **Warn only from this allowlist** — backend `/status` non-200 or refused (`TERMNORM_URL` in `.env` is the
  backend, default `:8000`; the PromptPotter API on `:8001` has no `/status` and 404s there); the
  active pointer naming a different dataset than requested; recent
  `measurements/cells/{config_key}.jsonl` showing empty `predicted` strings. Documented config is
  expected state, not a warning.
- **Bounded retries are already handled.** `BackendClient.run_query()` retries a 5xx and a
  connection never made, 5 attempts, and a 429 whenever the run's backpressure lets it (the
  `Provider` row under Samples in flight), each admitted against the spend book; a read timeout
  is never re-sent. If 5xx still propagates, halt and say so — don't loop on top of the client's loop.
  **A `⚠ RATE-LIMITED` line in the readout is told to the operator at once**, with the time it
  names as the run's stop; its `✓ … answers again` line ends the hold.
- Error prefixes (`[CLIENT]` / `[SERVER]` / `[CONNECTION]` / `[PIPELINE]`) → the cycle's `readout.log`
  and the latest `rounds/round_NNNN.json`.
- Surface the kill command (`tasklist | findstr python` → `taskkill //F //PID <pid>`) only when
  recommending a long-running launch in *this* turn. If a CLI call auto-backgrounds, kill it before
  retrying.
- **Never wipe project data without asking** — spell out the full path first.

## References

- [reference/onboarding.md](reference/onboarding.md) — new-dataset flow (web + CLI), Claude-simulated check-in, cold-machine bootstrap
- [`promptpotter/application/optimizers/potter/CLAUDE.md`](../../../promptpotter/application/optimizers/potter/CLAUDE.md) — the L1/L2/L3 agent contracts: what each layer reads, writes and decides
- [`docs/operations/persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run — the triage order when a run is stuck; stop-reason recovery
- `/potter-self` — running + supervising `promptpotter-self`; [`docs/specs/l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md) for what its numbers may claim
- [`docs/concepts/the-loop.md`](../../../docs/concepts/the-loop.md) · [`docs/developer/self-healing-internals.md`](../../../docs/developer/self-healing-internals.md) · [`docs/operations/persistence-and-state.md`](../../../docs/operations/persistence-and-state.md)

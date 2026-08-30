---
name: potter-run
description: Runs and supervises PromptPotter optimization campaigns — launches (`new` / `resume`), reads a live run's dashboard and round files, diagnoses what the loop produced, and onboards a new dataset. Fires when the operator says "/potter-run", "start a campaign", "run promptpotter-self", "resume", "watch it", "how's the run", "why did round N do that", or names a dataset to optimize — and equally when a campaign is ALREADY in flight and needs supervising mid-conversation, with no fresh-start ceremony. Re-reads run state from disk on every entry, then declares its mode in one line: bug-hunting (the default in this repo — the run is an instrument, the bug is the deliverable) or campaign-supervision (the run is the product).
model: opus
---

# potter-run — launch, supervise, diagnose

You are PromptPotter's data-scientist operator. Campaigns find better prompts and pipeline
parameters for LLM-powered evaluation pipelines; this skill runs them and reads what they produce.

**Argument (optional):** a registered benchmark name (`bbeh`, `aime_2025`, `justlogic-d234`,
`promptpotter-self`, `lca-termnorm`), a raw file path (`./data/bom.csv` → [onboarding.md](reference/onboarding.md)),
or nothing — with nothing, the entry read below decides what to do.

## Mode — declare it, never ask

Mode is a global variable the context already sets. Infer it, state it in the entry line, and
carry on. One word from the operator flips it.

**bug-hunting — the default in this repo.** The run is an INSTRUMENT; the deliverable is the bug.
Signals: `promptpotter-self` / `justlogic-d234`, the L4 finish line, a dirty tree, "why", "what
broke", "bug-hunting", an operator already mid-investigation.
- Read past the headline into the round's LLM I/O. A green accuracy over an empty panel is a finding.
- Authority: halt a run, fix at its ROOT, relaunch — without asking. Name the structural cause
  before touching code; default the fix to the `promptpotter/assets/optimizer/` optimizer prompts
  (`pipeline.yaml::resolved_prompts` inner, `sets/*.yaml` outer) (`<root-fix>`, `<dispatch-first>`).
- **Never commit.** Fixes accumulate uncommitted; name every path touched so the operator can
  `git add` by path (a second session commits to `main` concurrently).

**campaign-supervision.** The RESULT is the deliverable. Signals: a tenant dataset, a fresh ingest,
"just watch it", "don't touch anything", an operator who wants numbers not causes.
- Report and interpret. Don't touch prompts, config, or code. Don't kill the run.
- Defer to configs — documented state is not a warning.

## Entry — identical cold or mid-conversation

There is no fresh-start ceremony and no audit to replay. Whatever the conversation already
covered, re-read state from disk — that is what makes turn 0 and turn 100 the same entry:

1. `projects/{tenant}/.workspace/active_session.json` → `{session_id, campaign_id, cycle_id}`
2. that cycle's `dashboard.json` (`run_phase`, `round`, `best`, `hearts`, `error_count`) + the
   newest `rounds/round_NNNN.json`
3. `logs/latest.log` tail — the most recent run's terminal readout, ANSI-stripped

Then **one line**: `mode · what's live · next action`. Nothing else before it.

Reads happen by opening files; `evidence` is the one read VERB, because a comparison ACROSS campaigns is in no single file. Campaign detail lives in
`campaigns/<campaign_id>/{campaign.json,dashboard.json,log.md}`, per-cycle detail in
`cycles/<cycle_id>/{index.json,log.md,rounds/}`, per-round node I/O in
`.runtime/cache/rounds/round_NNNN.json`. **Open JSON as UTF-8 explicitly** — a default read on
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

The operator runs the command in their own terminal; campaigns take minutes to hours, so never
wrap one in Bash and never `run_in_background`. Other CLI calls: 30 s default timeout, 60 s hard
max — ask before exceeding.

| Verb | Behavior |
|---|---|
| `new <name>` | Registered benchmark. Mint a fresh Campaign + root cycle from `datasets/<name>/`, decompose `task_description.md` on first sight, run from round 0. Distinct `campaign_id` per invocation; the prior campaign is preserved. |
| `new <file>` | Raw ingest — parse → `--set` → resolve origin → commit tenant dataset → mint + run. See [onboarding.md](reference/onboarding.md). |
| `resume` | Continue the active cycle from the tenant pointer. `--from N` rewinds in place. |
| `set-budget` | Raise (or lower) an existing cycle's ceiling: `--max-usd` / `--max-tokens`. |
| `pause` | Ask a RUNNING cycle to stop at its next checkpoint — resumable, and the same dispatcher verb the webapp control fires. This is the HALT this skill keeps asking for. |
| `verify` | Re-score one candidate on more samples and record the result WITHOUT touching the cycle. The sanctioned way to settle a candidate — never re-ask a cell it already answered. |
| `evidence` | Read any set of campaigns together: roster, comparability, replicates, the variance split, resolving power, and (behind `--ranking`) which edits beat their own origin. Zero spend, writes nothing. |

**A budget halt is not the end of a run — it is two verbs.** `SPEND_BUDGET` / `TOKEN_BUDGET` mean
the cycle hit *its own declared ceiling*, not that the work is done. Only `spend_budget_usd` is
armed by default, so a campaign that declared nothing stops on dollars — `token_budget` is `None`
until set. Continue with `set-budget --max-usd <higher>` then `resume` — the ceiling is composed
over the config at the next launch and the wallet still bounds it, so a raise sticks. Two things
to check before assuming it worked: the ceiling is clamped against the account allowance, so read
the ARMED value back off `dashboard.json::run_limits` rather than trusting the number you sent;
and the counter is CUMULATIVE across resume, so the new ceiling must exceed the total already
spent, not the work remaining.

Flags come from `datasets/{name}/dataset.md § Init Flags`, verbatim — never guessed. `new`
overwrites the tenant pointer; `resume` is the happy path and needs no flags. Stop with Ctrl+C:
first cancels the in-flight call and pauses (resumable, exit 130), second force-quits. Every query lands in
`measurements/runs/{run_id}.jsonl` immediately, so a hard kill loses zero work and `resume`
cache-hits prior results.

**Launch discipline for `promptpotter-self`** — owned by the `potter-self` skill
(§ Live-run supervision), which states it in more depth: once, foreground, to completion; `resume` to
iterate; `new` again only after a prompt or config change. Read it there; it is not
restated here.

## Supervise

**Self-firing cadence, ~150–270 s** (`ScheduleWakeup`), set the moment a run starts. Each wake is a
full reading pass — a log-tail grep is not a checkup, and a passive Monitor is not supervision:
every real bug so far was found by reading the run's own measurement files, not by a pattern hit.
The interval is for *fanning out and researching*, not for idling.

For `promptpotter-self`, the per-tick reading list is the `potter-self` skill
(§ The per-checkup reading list) — read it there; it is the source of truth and is not restated here.

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

Surface the live paths so the operator can open them directly, and recommend the webapp preview
(`python -m uvicorn promptpotter.main:app --port 8001` → <http://127.0.0.1:8001/>, polls
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
`cycles/<id>/index.json` (`best_accuracy`, `best_round`, `origin_accuracy`, `final.winner_*`,
`final.stop_reason` — its display label and outcome class come from the one
`STOP_REASON_INFO` table, `promptpotter/domain/phases.py`).

### A held round is not proof the candidate failed — check the other estimator

**The hit sequence is difficulty-ordered, so a tail of 1s is the ORDER, never a surge.**
`build_round_order` (`intelligence/adaptive_queue_mechanism.py`) puts parent-MISS win-opportunities
first (ascending δ), parent-HIT regression probes every 4th position, and cells the parent never
answered last, by discrimination — never as misses, which front-loaded the easiest cells. So every
arm ends `…1111111` and opens near zero: the late run is the bank, not momentum, and paired
against a parent that also wins those rows it carries no information.

**The promotion gate and the PoBB posterior can disagree — they are asking different questions.**
`p_better` is a **stopping** posterior (is more measurement worth buying?); `improved` is a
**promotion effect-size** gate (is the lift big enough to adopt?). Both read θ on the locked δ
ruler — `headline_metric` is DISPLAY config, never what the gate compares. They can legitimately
disagree without either being broken. Read both, name both.

**A number can be set by where you STOPPED — ask what CHOSE the rows.** `matched_parent_*` strata
are defined by the *parent's own* grades, so on a truncated prefix the score is fixed by
construction rather than by the data (one HIT-stratum slot every 4th position ⇒ a cut arm reports
`⌊n/4⌋/n`). `scoring/metrics.py::matched_parent_stats` now returns `None` unless the candidate
covered the origin's panel, so a cut arm reports where it stopped plus its θ, never a standing.
**A `matched_parent_accuracy` on a row whose `scored_samples < expected_samples` is a pre-fix
artifact — do not quote it, and do not compare it across arms.**

What the ordering does **not** do is starve the posterior — `p_best` moves across most of the
budget, so the stratification is not why ε fails to fire. Arms that end close are close. Checked
and refuted; don't re-run this hypothesis.

So: **when a round reports `improved: false`, open
`.runtime/streams/round_NNNN_p_best.jsonl` and read the final `paired_breakdown` before accepting
it.** A held round whose `p_better` sits far off 0.5 is a promotion the gate refused, not a
candidate that failed. Report it as an instrument disagreement, and name both numbers.

Do **not** answer this by retuning `pobb_epsilon`, and do **not** route promotion through
`headline_metric` — that knob is display-only on purpose, and the gate is already θ. A held round
now means exactly one thing: no candidate's ability exceeded the parent's. If that still looks
wrong after reading both numbers, ask what the round measured, not which estimator the gate uses.

## Configs are the source of truth

The skill carries no parallel default-ladder. `dataset.md` (entry point, init flags) ·
`campaign.json` (max_rounds, n_variants, sp_budget_ttest, patiences) · `pipeline.yaml` (pipeline,
model, caps). BBEH only: `notebooks/bbeh_potter.ipynb::build_campaign_config()` shadows
`campaign.json` and wins. Per-dataset model + `reasoning_effort` + `max_tokens` defaults live in
[`docs/operations/dataset-reasoning-matrix.md`](../../../docs/operations/dataset-reasoning-matrix.md).
The `pipeline.yaml` `model` field is a live operator knob (Groq daily-volume swaps 120b → 20b), not
a fixed default. `max_tokens` is never set numerically in node configs — provider ceiling applies;
override per-cycle via `campaign.yaml::pipeline_overrides`.

Read them. Don't propose parameter tweaks unbidden, don't classify data volume, don't offer
leaderboard picks.

## Style

- **One shape: a sentence, or a compact box, or 3–5 bullets.** Combine only to put an anomaly flag
  above the state line. Interpret results; never dump CLI output.
- **Warn only from this allowlist** — backend `/status` non-200 or refused (`{backend_url}` is the
  backend, default `:8000`; the PromptPotter API on `:8001` has no `/status` and 404s there); the
  active pointer naming a different dataset than requested; recent
  `measurements/runs/{run_id}.jsonl` showing empty `predicted` strings. Documented config is
  expected state, not a warning.
- **Bounded retries are already handled.** `BackendClient.run_query()` retries 429 (Retry-After)
  and 5xx/transport with backoff, 5 attempts. If 5xx still propagates, halt and say so — don't loop
  on top of the client's loop.
- Error prefixes (`[CLIENT]` / `[SERVER]` / `[CONNECTION]` / `[PIPELINE]`) → `output.log` + the
  latest `rounds/round_NNNN.json`.
- Surface the kill command (`tasklist | findstr python` → `taskkill //F //PID <pid>`) only when
  recommending a long-running launch in *this* turn. If a CLI call auto-backgrounds, kill it before
  retrying.
- **Never wipe project data without asking** — spell out the full path first.

## References

- [reference/onboarding.md](reference/onboarding.md) — new-dataset flow (web + CLI), Claude-simulated check-in, cold-machine bootstrap
- [`promptpotter/application/optimization/CLAUDE.md`](../../../promptpotter/application/optimization/CLAUDE.md) — the L1/L2/L3 agent contracts: what each layer reads, writes and decides
- [`docs/operations/persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run — the triage order when a run is stuck; stop-reason recovery
- `/potter-self` — running + supervising `promptpotter-self`; [`docs/specs/l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md) for what its numbers may claim
- [`docs/concepts/the-loop.md`](../../../docs/concepts/the-loop.md) · [`docs/developer/self-healing-internals.md`](../../../docs/developer/self-healing-internals.md) · [`docs/operations/persistence-and-state.md`](../../../docs/operations/persistence-and-state.md)

# faults-1: a refusal carries its acts, one answer to a form failure, the logs no one needs go (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `faults-1`, arc `arc/faults-1`.

- **Base:** `origin/main` at `dce5b7883`: main with readings, xconn 1–2, ws and narr-2 landed. Your `<base sha>` in common.md is this commit.
- **The code shrinks**, judged as common.md says. Link 3 is mostly deletion: 45 log calls go. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** the ledger section `### batch/28 · faults`, these rows only:
  - "A refusal carries prose, a stop carries acts";
  - "`ValidationFailure.reason`'s description lists its codes by hand";
  - "A text node's form failure has three answers";
  - "The wound map has no home";
  - "A deep YAML ends the process, never a refusal";
  - "Reported beside faults-w".

  Re-run each Re-test first, at your base. Q3 ("one fault channel") and the `derived_file_stale` row are faults-2's, after narr-2: leave them, except the log deletions below, which shrink Q3's count.

## Decisions (orchestrator; build them, do not reopen them)

- **A refusal carries acts, as a stop does.**
  - `domain/refusal.py::Refusal` gains `next_acts: tuple[NextAct, ...]`, the type `domain/phases.py` already gives a stop. Each door spells an act: the CLI its verb and flags, the API and webapp a command kind, MCP its tool.
  - No refusal message names a verb or a flag. `phases.py:522`'s rule extends to refusals, held by a gate check.
  - The 20 served refusals that spell a CLI flag or verb, and the ~12 409s that name no next step (the row lists both), carry their act instead. `mint-campaign` is said nowhere.
  - This subsumes the `ConfigDriftError` row: close it.
  - `Source.of` raises a refusal naming the field it lacks (`loaders.py:87,104`). The listing serves that refusal on the dataset's row, and a start answers 422, never 500.
  - `OPTIMIZER_ABORT` carries the optimizer's reason and an act. Its reason is served, not only logged (`escalation.py:516-531`).
- **The arms gate's answer is an act.** narr-2's readout dropped the `[ARM GATE]` block's
  `answer: python -m promptpotter submit-arms …` line; today only `runner/round.py`'s
  `logger.warning` tells a terminal how to answer, and it is on your log list. The gate's row carries
  a `NextAct` (`submit-arms`), the readout spells it as `stop_next_lines` spells a stop's, and the chat's
  decision card (`domain/activity.py::_arms_gate`) stops spelling `submit-arms` and `--keep` in prose.
  That log goes only after the readout names the act. Re-test: an `arm_gate: on` readout at its hold
  names `submit-arms`.
- **One answer to a text node's form failure: the child is refused, as a fault value.** capo's init and gepa's `fenced` read it like every other text node. Decisions move on the `wounded/` legs only: name each.
- **`ValidationFailure.reason` is typed by an enum of its codes.** The hand list in its description goes. A move of the requests that carry the schema is expected: name it.
- **The wound map** becomes `domain/results_health.py::classify_result`'s docstring, from `git show d9012dc0e:docs/developer/self-healing-internals.md`: the two-axis table and the two row rules, no more.
- **One depth bound where `store/io.py::parse_yaml` reads**, so `"["*5000+"1"+"]"*5000` raises `ValueError` there. Size the bound from the deepest shipped YAML with headroom, and name it.
- **The reported items: verify each first** with a falsifying check. Build those that reproduce at their root, and name those that do not as not reproduced.
- **A log goes when no reader needs it, or when a typed record already holds its fact.**
  - An ERROR or CRITICAL log has a reader: `webapp/e2e/serve.mjs` fails a walk on one. It stays until faults-2 makes its fault a value, unless its record twin is verified.
  - The lists below are a Sonnet census at `092418bd6`, so they are leads, not orders. la and readings have since moved the lines: re-find each at your base. Verify every twin at the line before deleting its log, and name any lead you keep, with the reason.
  - **33 traces no reader needs:** `loaders.py:445,461`; `account.py:245`; `wiring.py:279,285,294`; `jobs/mint.py:583,706`; `reaper.py:54,83`; `head_to_head.py:187`; `launch.py:76,193`; `pipeline_resolve.py:486,1260`; `run_job.py:119`; `admission.py:407`; `indexes/sample.py:246`; `mint_and_start.py:110`; `cell_envelope.py:43`; `runner/loop.py:105`; `runner/round.py:549,1062`; `search_point_scorer.py:119`; `passes.py:352`; `sample_measurement.py:105`; `reset.py:206`; `query_loop.py:513,787`; `ab_replay.py:223`; `fork_siblings.py:81`; `validators.py:479`; `difficulty.py:58`.
  - **12 that repeat a typed record:** `runner/termination.py:76,78,84,90,92` (the `RunPhaseRecord` and `ErrorRecord`); `query_loop.py:406` and `runner/loop.py:200` (the stop reason); `runner/round.py:217` (the arms gate's phase); `dispatch/facade.py:98` and `escalation.py:410` (their `RoundWarningRecord`); `escalation.py:456,531` (the stop and the `ForkSpec`).
  - A module left with no logger loses its `import logging` and `getLogger`. Any log you find that a reader does need is named in the report; it is not deleted.

## Links (one commit each, in order)

1. A refusal's acts: `Refusal.next_acts`, the gate rule, the 20 + ~12 refusals, `ConfigDriftError`, `Source.of`, `OPTIMIZER_ABORT`, the arms gate's act.
2. One answer to a form failure; the `ValidationFailure.reason` enum; the wound map's docstring.
3. The YAML depth bound; the reported items; the 45 logs.

## Beside you

Name every shared file in its link's commit body.

- **narr-2 is landed in your base.** The ledger keeps facts, and a cycle's acts come off its admission. You own `Refusal`'s acts, `STOP_REASON_INFO`'s `next_acts` and the gate rule. Leave narr-2's `parked` and its per-cycle acts in `application/hierarchy.py` as they stand. Re-find your link 3's log lines in `runner/round.py`, since narr-2 rewrote its writers.
- **xconn 1–2 is landed in your base.** Its two refusals are yours under the gate rule, like every other refusal: `wire_key_unknown` (a node's served wire keys bound what is sent) and harbor's refusal when `skill_delivery` is unset. Its served-contract probe (link 3) waits on `wip/xconn` and is not yours.
- **small-2** (cloud, same base) moves `optimizers/nodes.py::Selector`'s parent read to a default
  (levi's and potter's overrides go) and types `spend_book.py`'s remainder. Leave both; if your link 2
  reaches `optimizers/nodes.py`, keep your hunks off `Selector`. Both of you cut rows in
  `code-debt-cleanup.md`: delete only your own rows.
- **ren** (cloud, later) waits for you.

## Proof (beyond common.md)

- Link 1: equals-main UNMOVED on every leg. Each changed refusal, before and after, as the CLI and the API serve it.
- Link 2 moves only `wounded/` legs and the requests carrying the schema: name each moved leaf.
- Link 3: equals-main UNMOVED on every leg, behaviour keys unchanged.
- `git grep -cE "logger\.(warning|error|exception|info|debug)\(" -- promptpotter/application promptpotter/domain`, before and after (130 at `092418bd6`).
- `git grep -l "^import logging" -- promptpotter/application promptpotter/domain | wc -l`, before and after.
- Each row's re-test, before and after.

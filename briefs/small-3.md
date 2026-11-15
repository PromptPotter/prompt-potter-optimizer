# small-3: a proposer reads its round's samples through its context; one meaning of "panel"; an optimizer model's source (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `small-3`, arc `arc/small-3`.

- **Base:** `origin/main` at `28956e323` (model-lines landed: `optimization.call_target`). Your `<base sha>` in common.md is this commit.
- **The code shrinks**, judged as common.md says: twelve unused parameters and their pass-through go. Run
  `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** `### batch/31 · small`, three rows only:
  - "`propose` hands every optimizer the round's samples only gepa reads"
  - "\"panel\" names two things"
  - "An optimizer node's model serves `source: \"unset\"`"

  Re-run each row's Re-test first. **Not yours:** archive hygiene, the paper harness, any other row.
- **Beside you:** **mkey** (landing now) and later **faults-2a**. They work in `api/`, `account.py`,
  access, `command_kinds.py`, `dispatcher.py` and `shared/errors.py`, not in your files. A file you both
  need is named in your commit body, and you touch only your hunk.

## The census (orchestrator, at main d118df305; re-grep it on your base)

- **propose:** `application/optimizers/nodes.py::Proposer.propose(ctx, panel: RoundSamples, proposals)`.
  - Twelve of the thirteen implementations ignore it (`_panel`): capo ×6, levi ×3, variation ×2, potter ×1.
  - Only `gepa/members.py:208` reads it.
  - The chain that passes it: `runner/round.py::_walk`, `::_walked`, `::propose_population`, `::_proposed`.
  - The door a member reads the cycle through: `bench/node_context.py::NodeContext.read` with a `CycleRead`
    named in the member's `sees`. Its sibling `NodeContext.access` / `drawn` reads a draw's pool.
- **panel:** the word holds several things.
  - A set of cells: the origin panel (`domain/origin_panel.py`), the L4 panel, a round's drawn
    `RoundSamples` (`panel: RoundSamples` in `runner/round.py`, `race(ctx, panel)`), and
    `MeasurementRole.PANEL`.
  - Other things: `domain/results.py::RoundResult.panel` (a round's arms), `domain/arm_reading.py`'s
    `panel: ArmSamples`, `domain/results_health.py:344` `panel -> int`, and `dataset_access.py`'s
    `panel: dict`.
  - `assets/optimizers/potter/pipeline.yaml` (22 uses) is prompt text an LLM reads.
- **The source:** `application/pipeline_resolve.py::laid_config` builds `knob_layers` from
  `optimization.nodes` stamps only; `domain/pipeline_schema.py::node_config_schema` serves
  `stamped.get(key, "unset")`. The call target lands in `optimizer_manifest.py::_select` through
  `PipelineManifest.calling`.

## Decisions (orchestrator; build them, do not reopen them)

- **A proposer reads the round's samples as a declared read.** gepa names a `CycleRead` in its `sees`, and
  `NodeContext.read` hands it the round's drawn `RoundSamples`. `Proposer.propose` takes `(ctx, proposals)`,
  and the parameter leaves every implementation and the runner's pass-through.
  - The draw reaches the read on the one context the round already holds (`RoundContext`), never a second
    channel beside it.
  - Where `RoundContext` carries it, the round stops passing it separately to every reader the context
    already reaches. Name what still takes it, and why.
- **"panel" is a set of cells.** The origin panel, the L4 panel, a round's draw and `MeasurementRole.PANEL`
  keep the word. Any other member is renamed for what it holds: a round's arms, an arm's samples, a count.
  - Prompt text an LLM reads is out of scope. A prompt edit is a measured change and rides tune 1, like
    "slot". File it as one line on that row.
  - A renamed field on run data wipes it: name the wipe. Never write a reader of two shapes.
- **The call target is the source it is.** Each optimizer llm node's `provider` and `model`, when its own
  config leaves them to the call target, is served with the layer that set `optimization.call_target`.
  - Make that stamp in the fold that already stamps (`laid_config`'s `knob_layers`), never a diff in a
    reader. A key a node's own config sets keeps its own source.

## Links (one commit each, in order)

1. **The declared read:** `propose(ctx, proposals)`, gepa's `CycleRead`, and the runner's pass-through gone.
2. **One meaning of "panel":** the renames, and the tune-1 line for the prompt text.
3. **The call target's source:** the stamp, and the I9 row it serves.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg: no decision or request moves. A trace key renamed by link 2 is
  named; give its old → new and its count.
- Digests: name every one that moves, old → new. Every move must be by spelling alone.
- Each row's re-test, before and after.
- Link 3 gets a test only if it meets `tests/CLAUDE.md`'s charter. Otherwise name the read you checked by
  hand (`/pipeline?at=` on an offline campaign).

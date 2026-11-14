# valid-3: valid-2 re-ordered, deletions first; the fork's admission order goes back to admit-2 (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Then read `briefs/valid-2.md` on the same branch. Its decisions bind you except where this brief changes them. Chain id `valid-3`, arc `arc/valid-3`.

- **Base:** `origin/main` at `de4c0cc47480f00b7b4e37fa015681c9ddddc0ad`. Your `<base sha>` in common.md is this commit.
- **Prior work:** valid-2 stopped at its link 1 on the shrink rule.
  - Its report is `origin/out/valid-2-report:report.md`.
  - Its link 1 is `origin/wip/valid-2` (d75eb7e), at +323/−278 (+45).
  - Reuse its hunks for the decisions that stand (below). Never cherry-pick the commit whole.
- **The code shrinks:** the chain's net over `promptpotter/` is at most 0, and the target is ≤ −40.
  - Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
  - A link that leaves the running total positive gets one rebuild, then the chain stops there.
  - Report the number per link.

## What changes from valid-2.md

### The order, deletions first (one commit each)

1. **A draft becomes its cycle** (valid-2's link 2, as written).
2. **One mint record, one move onto a fork** (valid-2's link 3, as written). It rebuilds `wip/valid`'s slice too.
3. **The fork door without the admission reorder** (valid-2's link 1, cut down as below).
4. **A copy revalidates** (valid-2's link 4, as written). It lands only while the total stays negative.

### Link 3 drops the admission reorder

The measured cost of admitting a fork before its cut is about +60 under today's admission model: the `OperatorFork` launch kind, `CycleHop.resolved`, and the unresolved-hop branches in `quota.py` (`_landed`, `share_campaign_ceiling`, `_refuse_unshared`) and `admission.py`. That flag keeps two hop states apart. It is the patch shape of the missing root: one admission door, where a fresh mint's `UNRESOLVED_HOP` and a fork's not-yet-cut child are one state. That door is admit-2's.

- **Do not build:** `OperatorFork`, `armed_on`, `CycleHop.resolved`, or the quota and admission branches.
- **On main, a fork is cut, then admitted, and withdrawn on a refusal** (`ForkCut.withdraw`, `_unfork`). That stays.
- **The admit-2 row "A fork's cut lands before its admission" stays open.** Append one sentence to it: the measured +60, `wip/valid-2` d75eb7e as the reference build, and "rides admit-2's one admission door".

### What link 3 keeps (reuse `wip/valid-2`'s hunks)

- **The CLI's own fork rules go**, and one builder lays the whole payload. That is the −49 in `resume_command.py`.
- **`fork_siblings.py::operator_seed` seeds X's prompts and params.** `SteerForkPanel.tsx` stops sending unedited prompts.
- **`HeldRun.mode` comes from the admission**, and no door re-derives it.
- **A served act carries the payload it fills.** This is narrower than the wip:
  - The act derives its payload in one place: `NextAct.payload`, from its own field and value.
  - `RunAdmission` serves its acts' payloads, and `KeepDraftsAction` carries `payload`.
  - No dotted-field `partition` runs inside `RunAdmission.of`.
  - `useRunControl.ts` and `LiveSegment.tsx` send what is served, unchanged.
- **`verbs.py::_unsaid` checks nested payload fields.** The wip spent +18 lines here; if your version still grows, say why in one line.

## Rows

- Close the five `### valid` rows in the links that close them.
- Delete the stale steer row ("A steer is typed, then sent untyped").
- File the three rows valid-2 filed, re-worded to your tree:
  - one fact, two append loops;
  - `HeldRun.of`'s second config read;
  - a fork's share lands at its landing.

## Beside you

The `rel` chain (`arc/rel`) runs on `scripts/gate.py`, `.githooks/`, `.github/`, `pyproject.toml`, `uv.lock`, `paper/` and `config/settings.py`. Touch none of them.

`docs/specs/code-debt-cleanup.md` is shared by hunk.

## Proof

As `valid-2.md` § Proof, with two changes:
- the refused-fork re-test is dropped, because its row stays open;
- the wipe is named in link 1 or 2, whichever changes the form first.

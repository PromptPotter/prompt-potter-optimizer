# One served hierarchy — stages 2 to 4

> **Scope.** The rest of the one hierarchy `campaign → course → round → candidate → campaign`, then
> steps and time lanes, then the dashboard read slimmed to run control. Stage 1 (campaign and course
> entries, `GET /hierarchy`) has shipped and is a contract now:
> [`frontend-surface-contract.md`](frontend-surface-contract.md) § The served hierarchy and
> [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § The hierarchy
> address. The arc's record is [`roadmap.md`](roadmap.md) § One served hierarchy. A
> `<!-- target: … -->` is a probe the build arms by renaming it `probe:` in the commit that makes it hold.
>
> **Binding, from the operator (2026-10-24):** one hierarchy, no adapter layer; "eliminate as much code
> as possible"; backbones and modules, zero indirection, no backward compatibility. Dataset, optimizer,
> model, fork, diag and inner are TAGS, never levels. A fork is an EVENT in time: born inside its parent
> at a spawn point and drawn from there. Densities: sidebar row → hover card (the node at its newest
> moment) → run card (the same card, one layer down, at a chosen moment) → compare channel → time lane.
> On-disk data stays READABLE through 2026-10-25; served shapes and code change freely now.

## 0. Direction — the cost of the next change

| Probe change | Before stage 2 | After |
|---|---|---|
| **Add a field to a candidate** (say, its tokens' cost) | an `ArmNode` field + `_arm_node` + `_fold_contributions` carrying it across a renumber + the overlay copy + each TS reader re-joining the row: 5+ | one `CandidateFacts` field + one line in the candidate builder: **2** |
| **Add a stat to a candidate row** | a client wording helper per surface (sidebar row, hover card, forest tooltip): 3+ | one `StatKey` member + one wording line: **2** |
| **Add a level** (stage 3's step) | a new model, route, join and row component | one `Level` member, one facts model, one builder, one address segment: **4** |
| **Change how a fork is drawn** | the server fold (`_fold_contributions`, the renumber, the replay graft, `ForkStamp.cut_from`) and every client branch on `fork`: 6+ | the lane layout alone (`forest-layout.ts`), reading `facts.spawn`: **1** |

## 1. Stage 2 — round and candidate entries

### Decisions taken (§ 10's open items, recorded with their reason)

- **A fork is DRAWN, not folded.** Each course serves its own rounds and candidates; a fork stays a flat
  child course of its campaign and is drawn at `facts.spawn` on its parent's lane. *Why:* the operator's
  binding says a fork is an event drawn at its spawn, and folding is the one place the server rewrote
  another course's identity (labels renumbered across courses, a replayed C0 dropped, runs grafted). With
  every course serving its own record, a candidate's label is the one its course minted, and the fold,
  its renumber and its stand-in rows are deleted rather than reshaped. A fork's replayed C0 is an honest
  candidate of its own lane: it measured the origin individual again.
- **A retained candidate is a REFERENCE, never re-listed.** A round re-reads the individual it
  measures against as a `parent`-role arm; that arm is not a candidate of the round. The round names it
  by `RoundFacts.reference`, the address of the candidate that individual is, in this course or up its
  spawn chain. *Why:* one individual, one row per round that PROPOSED it. Re-listing a reference puts
  one configuration on N rows of a course, and every pointer (`answers_for_id`, `main_line`, a
  selection) would have to pick between copies of one measurement.
- **Parallel candidates are siblings in slot order, with no mark.** The candidates of one round are
  its children in `ArmSlot` order — the round's own order. *Why:* a round's arms walk concurrently,
  so any sequence among them is one the ledger interleaved, not one the optimizer chose; time order is
  stage 3's step entries, which carry offsets. A "parallel" flag would be a served bool true of every
  multi-arm round.
- **A step is addressed by its ledger offset** (stage 3, § 2). *Why:* an offset is the one identity a
  ledger line has that is immutable and totally ordered — the line index IS `sequence`, kept by every
  rewrite — and the moment (`?at=`) already speaks it, so a step, a moment and a fork's
  `spawn.at_offset` share one axis.
- **The sidebar draws campaign → course → candidate → inner campaign**, rounds flattened in the sidebar
  (they are the forest's columns), and a campaign with one course elides that course's row. *Why:* a
  round row adds a click per round to reach every candidate, and the forest already draws rounds.
- **`deletable_stubs` is counted on the subtree read only** — `CampaignFacts.deletable_stubs: int |
  None`, null in the listing. *Why:* `CampaignStore.deletable_stubs` scans every sibling's ledger per
  fork, so counting it per campaign on every 10 s listing poll is the cost the subtree read exists to
  keep off it; null says "not counted at this depth", never zero.

### Served model — `domain/hierarchy.py` grows

```python
Level = Literal["campaign", "course", "round", "candidate"]
TagKind += "verdict"                       # a candidate's ArmVerdict, worded ARM_VERDICT_LABELS
StatKey += "ability", "lift"               # a candidate's θ and its lift vs the reference

class _HierarchyEntry:                     # address, parent, label, lede, caveat, tags, stats, child_count, children
class _RunEntry(_HierarchyEntry):          # what only a level with a run carries
    state: HierarchyState
    span: HierarchySpan
class HierarchyCampaign(_RunEntry)         # unchanged facts, plus deletable_stubs
class HierarchyCourse(_RunEntry)           # facts gain origin, lens_criterion, lens_shift

class RoundFacts(FrozenModel):
    round: int
    elects_on: DisplayMetric | None        # the election's declaration; null on a round never elected
    reference: str | None                  # the retained candidate's address; null at round 0 and where none was read

class CandidateFacts(FrozenModel):
    individual: str                        # OptSearchPoint.id — NOT a key: two rows can share it
    parent_ids: list[str]
    variations: list[Variation]
    reading: ArmReading                    # `reading.arm` is THE per-cycle key every per-cycle document speaks
    stands: bool                           # the newest crowned live candidate of its course
    course_winner: bool
    course_latest: bool
    answers_for_id: bool                   # the ONE row tree-wide a remembered individual id resolves to
    main_line: list[MainLineStep]          # MainLineStep{round, candidates: list[address]}, the spawn chain's prefix first
    lens_value: float | None
    lens_rank_move: RankMove | None
    sample_set_accuracy: float | None
    sample_set_n: int | None
    divergence: LineageDivergence | None
    divergent: bool

class HierarchyRound(_HierarchyEntry):     level "round",     facts RoundFacts
class HierarchyCandidate(_HierarchyEntry): level "candidate", facts CandidateFacts
HierarchyEntry = HierarchyCampaign | HierarchyCourse | HierarchyRound | HierarchyCandidate
```

`children`: a course's are its rounds, a round's its candidates in slot order, a candidate's the inner
campaigns it spawned. `CourseFacts.origin` is the address of the course's own round-0 candidate, else
its spawn parent's origin — the arm the course's standing is paired against. `CourseSpawn.from_candidate`
becomes the parent candidate's ADDRESS: the fork record's `from_candidate_id`, else the reference of the
fork's first round, else the parent's origin. A retired candidate wears the `superseded` tag, its value
the address of the course that took its place (`_retired_by`, reading back only as far as the branch
got), and its `verdict` tag is `retired`. A candidate's `lede` is the server's sentence for its
verdict; its `caveat` is its θ caveat's head and body. `state` and `span` leave the envelope: a round
and a candidate have no run of their own, and a field null on half the levels is the "null because wrong
level" this model exists to refuse. `ForkStamp`, `ArmNode`, `CourseLineage` and the module
`domain/lineage_tree.py` die; `MainLineStep`, `LensShift`, `LineageDivergence` and `RankMove` move
into `domain/hierarchy.py`.
<!-- target: git grep -cE "class (ArmNode|CourseLineage|ForkStamp)\b" -- promptpotter == 0 -->

### Address

```
round     := course "/r" round_n                    bbeh__…::cycle_7f3a/r2
candidate := round "/" arm_key                       bbeh__…::cycle_7f3a/r2/<ArmKey>
```

Appended, never inserted, so every stage 1 address is unchanged. `(round, arm_key)` is the standing
rounds' own join (`StandingRounds.arms`), unique per course. An inner campaign's address stays
`{outer course}~{campaign_id}` and its `parent` refines to the candidate that spawned it
(`CycleIndex.spawned_by`: round and individual, in this course, else in any course of the campaign,
else the course's origin). `nodeAddress`, `nodeKeyOf`, `ownerOfNodeAddress` and the `|` separator die;
the webapp keys on `address` and decodes a candidate's with one codec in `lib/ids.ts`.
<!-- target: git grep -cE "nodeKeyOf|nodeAddress|ownerOfNodeAddress" -- webapp == 0 -->

### Where it lives

| Layer | Module | Holds |
|---|---|---|
| domain | `domain/hierarchy.py` | the models above |
| infrastructure | `infrastructure/store/lineage_queries.py` | the family walk (`iter_family_courses`, the ray's and the version's) and `read_course(stores, hop, *, moment)` — one course's arms in slot order with their readings, each round's election and reference arm; no topology |
| application | `application/hierarchy.py` | stage 1's campaign and course builders, `campaign_entry(…, moment=)` |
| application | `application/hierarchy_subtree.py` | `hierarchy_subtree(stores, address, lens, samples, *, moment)` — the deep read: the campaign's courses, their rounds and candidates, the retire and head stamps, the inner campaigns under their candidates (bounded at `_MAX_COURSE_DEPTH`), the lens overlay, then the entry at `address` |
| application | `application/mask/tree_lens.py` | the overlay, keyed by candidate address |
| presentation | `routers/hierarchy.py` | `GET /hierarchy/{address}` |

`application/hierarchy.py::family_course_entries` and `infrastructure/store/lineage_queries.py::build_lineage_tree`
die with the fold.
<!-- target: git grep -cE "def (build_lineage_tree|family_course_entries|lensed_tree|_fold_contributions)\(" -- promptpotter == 0 -->

### Routes

**Added.** `GET /hierarchy/{address}?lens=&samples=&at=&at_cycle=` → `HierarchyEntry`: the subtree at
any address, a campaign's whole family down to the inner bound, 404 where no entry stands. `lens`,
`samples`, `at` and `at_cycle` are the tree route's, unchanged. One conditional read keyed by the
address over the campaign family's `tree_version`, so a client polls only the campaign it opened.
**Deleted.** `GET /campaigns/{c}/cycles/{cy}/tree`.
<!-- target: git grep -cE "/tree\"" -- promptpotter/presentation == 0 -->

### Consumers, five entry points

| Entry | After |
|---|---|
| **CLI** | `export --tier chat|report|notebook|package` reads the course subtree at the cycle it names (`application/cycle_reads.py::export_cycle` → `application/views/render/export.py` over `HierarchyCourse`): the candidates that course minted, live side of each supersede cut, by their own labels. `cycles` unchanged. |
| **`/potter-run`** | unchanged — it names no tree field. |
| **Embedded** | a host imports `hierarchy_subtree` from `application/`. |
| **REST** | the route above; `openapi.generated.json` regenerates. |
| **Webapp** | the table below. |

| Surface | Reads after stage 2 |
|---|---|
| `lib/api/reads.ts`, `lib/lineage.tsx` | `subtreeRead(address, mask, moment)` replaces `treeRead`; the viewed tree is the viewed campaign's `HierarchyCampaign`; a sidebar row reads its campaign's. `ViewedLineage.tree` is that entry. |
| `lib/derivations/lineage-candidates.ts` | `indexLineage(root)` → `{entries: address → entry, candidates: course address → candidates in round, slot order}`; `candidatesAtPath`, `armAt` (by `reading.arm.key`), `armOfId` (`answers_for_id`), `mainLineOf` (addresses), `originOf` (`CourseFacts.origin`), `splitRetired` (the `superseded` tag), `nodeOverlays` — over entries. `pathOf`, `armOfRow` die. |
| `lib/ids.ts` | `candidateAddress` codec: course address, round, arm key. |
| `components/candidates/forest-layout.ts`, `Forest.tsx` | a lane per course: the root at col 0, a fork on the same round axis hung at its `spawn.from_candidate`, an inner campaign's courses one column right of the candidate that spawned them; an expanded lane chains each round from `RoundFacts.reference`; `elects_on` from the round. |
| `components/shell/sidebar/ForestRows.tsx`, `RowHoverCard.tsx` | § Decisions' layout; a candidate row and its hover card render the served envelope — `label`, the `verdict` and `superseded` tags, `stats`, `lede`, `caveat` — and no client wording survives for it. |
| the remaining readers of `ArmNode` fields | `node.X` → `entry.facts.X`; `nodeKeyOf(node)` → `entry.address`: `candidate-views.ts`, `inner-panel.ts`, `lineage-descendants.ts`, `useLineage.ts`, `useCandidatesModel.ts`, `useCandidateBars.ts`, `FitnessChart.tsx`, `FitnessRankSummary.tsx`, `DendrogramStrip.tsx`, `dendrogram.ts`, `series.ts`, `compare/*`, `node-surface/*`, `searchPoint.ts`, `useSelectNode.ts`, `useOrigin.ts`, `candidates-store.ts`, `view-memory.tsx`, `types/candidate.ts`, `useRemoteModel.ts`. |
| `lib/test-fixtures.ts` and the lineage tests | `hierarchyRound`, `hierarchyCandidate` builders; `courseLineage`, `armNode` die. |

### Deletions, the ledger

Deleted: `domain/lineage_tree.py`; in `lineage_queries.py` the fold (`_contributions`, `_Contribution`,
`_fold_contributions`, `_empty_attempt`, `_stamp`, `_is_replay`, the graft, `_build`, `_with_heads`
moved and rewritten, `build_lineage_tree`, `_Reads.row`); `family_course_entries`; `lensed_tree`;
`get_lineage_tree`; webapp `treeRead`, `TreeMask`'s tree-only half, `nodeAddress`, `nodeKeyOf`,
`ownerOfNodeAddress`, `pathOf`, `armOfRow`, the client candidate wording in `ForestRows.tsx`, every
`fork` branch on a candidate. `scripts/complexity_ledger.py::BASELINE`: `modules` unchanged
(`domain/lineage_tree.py` out, `application/hierarchy_subtree.py` in); `served_fields` and `p_served`
re-stamped with their reasons in the commit that lands each.

### Tests

`tests/test_archive.py` § 3 reads candidates through `hierarchy_subtree` where it read `lensed_tree`:
the rewind step reads the line course's candidates and their `stands`; the unreadable-ledger reader
named `"tree"` becomes `"subtree"`. The stage 1 step comparing the tree's folded course to the listed
line dies with the fold — it guarded a choice no code makes now — and in its place one step on the SILENT
axis: after the supersede, the line course's standing candidate in the subtree is the one its index's
standing selected, because a tree and an index disagreeing about which candidate the next round
mutates from would draw one parent and run another, with nothing raised. Webapp: the lineage Vitest
files rewrite against the new builders; none is added.

### Builder split

1. **Docs** — this spec and stage 1's fold (one commit).
2. **The server serves it** — models, `read_course`, `hierarchy_subtree`, the overlay, the route,
   `export.py`, `test_archive.py`, the TS and spec regen. `/tree` still served and read: additive.
3. **The webapp reads it and the tree dies** — the table above, `/tree` and every § Deletions item,
   `webapp/lib/CLAUDE.md` (`/tree` row, the `nodeKeyOf` rule), `webapp/CLAUDE.md`,
   `infrastructure/store/CLAUDE.md` § The lineage tree, the `mask-projection.md` route. Probes arm.

### Live probes

Open a campaign with a supersede fork: the sidebar lists the root and the fork as courses, each
candidate under its own label, the retired tail marked; the forest draws the fork's lane from its spawn
candidate. A `?lens=` reorders, divergence dims, a moment cuts — each on the subtree read.

## 2. Stage 3 — step entries, spans, time lanes

`Level += "step"`. A step is one ledger record a surface draws on a time lane: an arm's walk opening
and ending, an election, a close, a fork's cut, an inner spawn — whatever `domain/activity.py::ActivityFeed`
already reads into an `ActivityItem`. Address `{course}/@{offset}` (§ 1 Decisions). `HierarchySpan`
gains `opened_offset` / `last_offset` on a course and a round. Steps ride under the course, not the
round: a step of a round's walk and the round's close interleave with another round's in one ledger,
and the round is a `step.facts.round` field, not a parent. The time lane draws a course's steps on its
offsets and a fork at `spawn.at_offset` on its parent's lane. Specified in full before it is built.

## 3. Stage 4 — the dashboard read slims to live run control

The masthead chip row, the run card and `run-summary.ts` read a course entry at a moment; the
`ServedDashboard` fields those surfaces read that a course entry carries die; its `RunStatus.of` folds
into `HierarchyState.of`, so the `RunStatus.of` probe becomes `== 2`. Specified in full before it is built.

# One served hierarchy — stage 1: campaign and course

> **Scope.** The two top levels of the one hierarchy `campaign → course → round → step → candidate | cell → inner course`.
> Stage 1 ships ONE served entry shape for campaign and course, minted in ONE module with ONE run-state
> derivation, and every surface that lists, switches, docks or heads a run renders it directly. Stages
> 2–4 EXTEND it (§ 10) and reshape nothing. The arc's record is
> [`roadmap.md`](roadmap.md) § One served hierarchy; this file is the contract. A
> `<!-- target: … -->` is a probe a later group arms by renaming it `probe:` in the commit that makes it hold.
>
> **Binding, from the operator (2026-10-24):** one hierarchy, no adapter layer; "eliminate as much code
> as possible"; backbones and modules, zero indirection, no backward compatibility. Dataset, optimizer,
> model, fork, diag and inner are TAGS, never levels. A fork is an EVENT in time: born inside its parent
> at a spawn point and drawn from there. Densities: sidebar row → hover card (the node at its newest
> moment) → run card (the same card, one layer down, at a chosen moment) → compare channel → time lane.
> On-disk data stays READABLE until 2026-10-26; served shapes and code change freely now.
>
> **Stage 1 has shipped (§ 9, groups 1a–3); every probe in this file holds.**

## 0. Direction — the cost of the next change

The target is the next change, not this one. Three probes, sites touched before → after stage 1:

| Probe change | Before | After |
|---|---|---|
| **Add a stat to a level** (say, a course's cells scored) | a `LineStanding` field + a `CycleListEntry` field + a `CourseLineage` field + `_line` + `_course_scalars` + a TS wording helper + each row/card that lists stats: 7+ | one `StatKey` member + one wording line in the level's builder: **2**; every row renders it unasked, server-ordered |
| **Add a level** (stage 2's round) | a new model, a new route, a new webapp join, a new row component | one `Level` member, one facts model, one builder function, one segment rule in the address minter: **4**; rows, hover card and switcher render it as they render a course |
| **Add a tag** (say, a `bench_set` chip) | a served bool + a client branch + a chip per surface | one `TagKind` member + one line in the builder: **2** |

Lines are reported (§ 7), never the goal.

## 1. The served model

`domain/hierarchy.py` — the envelope every level shares, its facts per level, and the one run-state
constructor. One `Hierarchy*` family — `HierarchyCampaign`, `HierarchyCourse`, and stage 2's
`HierarchyRound` / `HierarchyCandidate` — each an ENTRY, never a "node": "node" already means a
pipeline node everywhere in this repo (`NodePrompt`, `NodeKnobs`, `PipelineViewNode`), `ServedRound` is
taken, and a reader must pinpoint the concept from the name alone (root `CLAUDE.md` § STOP).

```python
Level = Literal["campaign", "course"]          # stage 2 adds "round", "candidate"; stage 3 "step", "cell"
TagKind = Literal[
    "dataset", "optimizer", "vendor", "mint", "lifecycle", "self_optimization", "arm", "superseded"
]
StatKey = Literal["spend_lifetime", "spend", "bench", "rounds", "accuracy", "lives"]

class HierarchyTag(FrozenModel):
    kind: TagKind
    value: str      # what a surface routes or branches on: the dataset slug, the vendor id, a CourseKind word
    label: str      # the chip, worded here

class HierarchyStat(FrozenModel):
    key: StatKey
    label: str
    value: str          # worded here: "≥ $1.20", "3 of 10", "+2.1 pp [−0.4, 4.6]"
    sub: str | None     # the second line: "origin → selection · rounds are won on θ"
    amount: float | None  # the served number `value` words, for a meter's geometry only — never re-worded, never re-ranked

class HierarchySpan(FrozenModel):
    opened_at: str
    last_at: str
    closed_at: str | None

class HierarchyProducer(FrozenModel):   # what a write moves, never a clock
    state: ProducerState
    label: str
    attached: bool
    appending: bool
    alert: ProducerAlert | None         # `ProducerAlert.of(state)`: the state's own words, no duration

    @classmethod
    def of(cls, reading: ProducerReading) -> HierarchyProducer: ...

class HierarchyState(FrozenModel):
    run_phase: RunPhase
    status: RunStatus
    producer: HierarchyProducer
    pause: PauseReading | None
    admission: RunAdmission
    stop_reason: StopReason | None
    intervened: bool

    @classmethod
    def of(cls, run: RunState, index: CycleIndex | None, *, lifecycle: LifecycleStatus) -> HierarchyState: ...

class _HierarchyEntry(FrozenModel):
    address: str                    # § 2 — server-minted, stable, the key of every row and every open set
    parent: str | None              # the hierarchy parent's address
    label: str                      # the display name, worded here: a campaign's label, else its dataset (today's `display_name` rule); a course's mint word and short id ("fork ·a1b2")
    lede: str | None                # the one sentence under the title: the line's `selection_line`
    caveat: str | None              # the one line a row shows when something could not be read
    tags: list[HierarchyTag]
    state: HierarchyState
    stats: list[HierarchyStat]      # server-ordered; `stats[0]` is the row's headline
    span: HierarchySpan
    child_count: int
    children: list[HierarchyEntry]  # `?depth=`; the union, so stage 2 adds a member and touches no field

class HierarchyCampaign(_HierarchyEntry):
    level: Literal["campaign"]
    facts: CampaignFacts

class HierarchyCourse(_HierarchyEntry):
    level: Literal["course"]
    facts: CourseFacts

HierarchyEntry = Annotated[HierarchyCampaign | HierarchyCourse, Field(discriminator="level")]

class CampaignFacts(FrozenModel):
    label: str | None               # the operator's own name; the envelope's `label` falls back to the dataset
    root: str                       # the root course's address
    line: str                       # the course holding the line — `CampaignStore.line_holder`, followed here and nowhere else
    lifecycle: LifecycleStatus
    spend_lifetime: LifetimeSpend
    runs_with: CampaignRunsWith | None

class CourseSpawn(FrozenModel):     # a fork is an EVENT on its parent's time line
    parent: str                     # the course it branched from
    at_round: int | None            # `ForkSpec.from_round`, which a fork may leave unnamed
    at_offset: int                  # `CycleIndex.forked_at_offset`
    from_candidate: str | None      # arm key — `at_round`, `at_offset` and this have no reader until
                                    # stages 2 and 3 hang a fork off its round, candidate and offset
    trigger: ForkTrigger
    direction: ForkDirection        # `ForkSpec.resolved_direction`: measured, or implied by the trigger
    reason: str
    issued_by: str                  # `ForkSpec.issued_by.display`

class CourseFacts(FrozenModel):
    mint: CourseKind                # root · fork · diag · inner — typed; the `mint` tag renders it
    spawn: CourseSpawn | None       # null on a root, and on a cycle cut from another tree
    task: str | None                # the inner cell this course answers (L4)
    standing: RunStanding | None
    rounds_closed: int

class HierarchyResponse(StrictModel):
    entries: list[HierarchyCampaign]  # root-major, newest campaign first; a campaign's children root first
```

**Why an envelope with level facts, in five lines.** A generic row, card, switcher entry or dock chip
reads the envelope and never opens `facts`, so no surface branches on level to draw an entry. One flat
model with every field optional cannot tell "null because wrong level" from "null because unread".
Per-level models with no envelope are what exists today, and the join they force is the adapter layer
being deleted. A discriminated union on `level` is the repo's own shape (`domain/results.py::ArmState`),
and TS narrows on it.

**Facts are typed inputs for a level-specific pane or a route argument; tags and stats are the
rendering.** Every chip and every number a row shows is served worded, in served order, under a served
key — the scoring authority (`docs/architecture.md` §0.5) now reaches the sidebar: the θ clause on an
accuracy stat, the "≥" on a floored bill, the "of 10" on a rounds count are worded at one site each.
<!-- probe: git grep -cE "rounds are won on" -- promptpotter/application/hierarchy.py == 1 -->
A `facts` field with no reader that names it is deleted, not kept for later (`scripts/complexity_ledger.py::BASELINE`
`p_served` is the ratchet).

**The producer is served without its clocks.** `/hierarchy` answers 304 on a version that moves on
writes and on the producer's beat edge (`CampaignStore.version()` stats every cycle's run-state stamps),
so a seconds count would freeze at the value of the last 200. `HierarchyProducer` carries what those
moves change — state, word, attachment, the alert's own words — and the durations (`silent_for_s`,
`open_for_s`, `call_holds`, `stalled`, a wedged alert's "for N min") stay on the dashboard read, which
polls the viewed cycle.

### Tags

| kind | on | value | label |
|---|---|---|---|
| `dataset` | both | the dataset slug (a route argument: `/datasets/{name}/cells`) | the dataset's name |
| `optimizer` | campaign | the manifest name (`runs_with.optimizer`) | same |
| `vendor` | campaign | the vendor id, one tag per vendor in `runs_with.vendors` | the vendor; models stay in `facts.runs_with` |
| `mint` | course | `root` / `fork` / `diag` / `inner` (`infrastructure/store/layout.py::sibling_kind`, the sandbox for `inner`) | "root", "fork", "diag", "inner run" |
| `lifecycle` | campaign | the status, `archived` or `deleted` — an active campaign carries none | the status's word, lower-cased (`LIFECYCLE_STATUS_LABELS`) |
| `self_optimization` | campaign | `"l4"` (`application/runner/inner/tasks.py::is_self_optimization`) | "L4" |
| `arm` | campaign | the comparison arm's key, a `Compare` purpose only | the arm's word |
| `superseded` | course | the successor's address — the pointer the line follows, served as the chip the retired parent wears | "retired by fork ·a1b2" |

The webapp asks a tag with two predicates, `hasTag(entry, kind)` and `tagValue(entry, kind)`
(`webapp/lib/hierarchy.ts`), and nothing else: `useLeafIsL4` is `hasTag(…, "self_optimization")`,
the viewed dataset is `tagValue(…, "dataset")`. A bool beside a tag is the duplicate this rule forbids.
**One declared exception: `mint`**, the only tag code branches on exhaustively (a glyph per kind, the
inner walk). A tag's value is an untyped string, so the course builder mints the kind ONCE and serves it
twice — typed as `facts.mint` (`CourseKind`), worded as the tag. Code reads the fact; the tag is worn.
<!-- probe: git grep -cE "tagValue\(.*mint" -- webapp == 0 -->

### State — one derivation

`HierarchyState.of` is the ONE packaging of `infrastructure/runtime_flags.py::derive_run_state` for a
served row, called once per course inside the course builder and once more for a line course with no
mint record (below). <!-- probe: git grep -cE "HierarchyState\.of\(" -- promptpotter == 2 -->
It stands where five models each re-packaged the same `RunState` today (`CycleListEntry`,
`LineStanding`, `CourseLineage`, `ServedDashboard`, `HeadToHeadRow`); after stage 1 `RunStatus.of` has three
callers — this constructor, the dashboard (stage 4 folds it) and the head-to-head row.
<!-- probe: git grep -cE "RunStatus\.of\(" -- promptpotter == 3 -->
**A campaign entry's `state` IS its line course's**, chosen by `CampaignStore.line_holder` and served
again under the course's own address: a campaign "runs" when its line runs, which is what the sidebar
mark has always meant. One builder words both, so they cannot disagree.

**Run state is read at the head, always** — a moment replays the record, never a process, so a course
minted at `?at=` carries its spend, standing, rounds and lives at that moment and its producer as it is
now.

**A campaign is always served.** Between `create_campaign` and the root's mint record (or after a mint
record is lost) the line course has no `CycleIndex`: the campaign entry still lists, its state from
`derive_run_state` over the line's directory through `HierarchyState.of(run, None, …)` (no stop reason,
not intervened), its line stats absent, and its `caveat` saying the line course has no mint record.
Dropping it would be a campaign the operator owns and no listing shows, with nothing raised.

### Stats

| key | campaign | course | worded from |
|---|---|---|---|
| `spend_lifetime` | headline, `stats[0]` | — | `domain/spend.py::LifetimeSpend` (billed, floor mark); `sub` "up to $X more unreported" where a send ended with no bill, else the rate-priced line |
| `spend` | — | headline | `domain/spend.py::MeteredSpend` over the course's own ledger, cut at the moment (billed, floor mark, the rate-priced line) |
| `bench` | ✓ | — | `application/runner/campaign_result.py::cycle_bench` |
| `rounds` | the line's | ✓ | `CycleIndex.rounds_closed` of the cap the course's armed run limits bind (`binding_run_limits(…).budget.rounds`), the declared `runs_with.budget_rounds` before its first launch — "3 of 10", "3 · no rounds axis", "origin only" |
| `accuracy` | the line's | ✓ | `domain/results.py::RunStanding` `vs_origin`; `sub` carries "rounds are won on θ" where the newest election elected on ability (`CycleIndex.elects_on`) |
| `lives` | the line's | ✓ | `RunStanding` `stalls_left` / `stalls_left_cap` |

A campaign's `stats` are its own two followed by its line's — composed, not re-worded.

### Span, children, fork

- `span`: a campaign's `created_at` → `updated_at` (closed when its line is terminal); a course's
  `created_at` → `updated_at` / `finished_at`. Stage 3 adds ledger offsets as fields here.
- `children` honours `?depth=`: `0` serves campaigns alone, `1` (default) their courses, root first.
  `child_count` is always served: a campaign's courses; a course's rounds the ledger holds — closed
  (`CycleIndex.rounds`) plus open (`CycleIndex.open_rounds`), which is the count stage 2's children will
  have — stage 1 serves `children: []` under a course at every depth. `?attached=true` keeps the courses
  a producer holds and the campaigns holding one; it refuses `depth=0`, which serves no course.
- **A fork sits in its campaign as a child course, with `spawn` naming the event**: the parent course's
  address, the round and the ledger offset it branched at, the candidate and the trigger, from
  `CycleIndex` + `domain/run_records.py::ForkSpec`. Where it is stored is unchanged (a sibling cycle dir);
  where it is DRAWN is `spawn` — the time lane (stage 3) draws it at `spawn.at_offset` on the parent's
  lane and nowhere else. A diag is a fork with its trigger's word. An inner campaign's `parent` is the
  outer course it sits under (`?descend=`); stage 2 refines that value to the candidate that spawned it
  (`CycleIndex.spawned_by`) — a value, not a field.

## 2. Address — server-minted, stable, hierarchical

```
campaign := [inside "~"] campaign_id                 bbeh__20261024-091500
course   := campaign "::" cycle_id                   bbeh__20261024-091500::cycle_7f3a   ≡ encode_cycle_path(path)
inside   := course ("~" course)*                     the L4 sandbox chain — today's `?descend=` wire, byte-exact
stage 2  := course "/" round_n [ "/" arm_key ]       appended, never inserted
```

One minter, `hierarchy_address(inside, campaign_id, cycle_id=None)` beside
`domain/cycle_paths.py::encode_cycle_path`, and a course's address IS its `CyclePath` on the wire — so
`encodeCyclePath(viewedPath) === entry.address` (`webapp/lib/ids.ts::encodeCyclePath`) and the webapp
mints no address of its own: it reads `address`, and decodes a course's with `decodeCyclePath` where a
route wants typed hops. A retired branch keeps its address for ever (a cycle dir is never renamed), which
is what makes the address the key of an open set, a React list and, in stage 2, a per-address ETag.
`nodeAddress` in `lib/ids.ts` keeps keying `ArmNode` rows until stage 2 retires `ArmNode`.

## 3. Where it lives

| Layer | Module | Holds |
|---|---|---|
| domain | `domain/hierarchy.py` | § 1 — the models and `HierarchyState.of` |
| domain | `domain/campaign.py` | `CampaignRunsWith`, `RunsWithParam`, `VendorModels` — beside `RunDeclaration`, so their producer (`application/pipeline_resolve.py`) does not import the served-entry module |
| domain | `domain/spend.py` | `UserSpend`, `ZERO_SPEND`, `LifetimeSpend` — domain facts `CampaignFacts` holds, so `domain/` imports no upper layer |
| domain | `domain/cycle_index.py` | `CycleIndex.open_rounds` and `CycleIndex.elects_on`, folded in the one standing scan `read_cycle_index` already runs |
| domain | `domain/cycle_paths.py` | `hierarchy_address` |
| application | `application/hierarchy.py` | `course_entry(stores, inside, campaign, cycle_dir, index, *, moment, runs_with, config)` · `campaign_entry(stores, inside, campaign, *, depth)` · `list_hierarchy(stores, *, inside, dataset, lifecycle, campaign_id, attached_only, depth)` · `hierarchy_version(stores, *, inside)` |
| presentation | `presentation/api/routers/hierarchy.py` | `GET /hierarchy` |
| presentation | `presentation/cli/commands/cycles.py::cmd_cycles` | the verb, printing course entries |
| webapp | `webapp/lib/registry.tsx::RegistryProvider` | ONE poll; `entries`, `byAddress`, `running`, `useEntry(address)` |
| webapp | `webapp/lib/hierarchy.ts` | `hasTag`, `tagValue` |

**Both builders are application-layer, in one module.** A course's `rounds` stat is worded against the
cap its run limits bind or the declared `budget.rounds`, which only `application/` resolves, and
`infrastructure/` imports nothing from `application/`. The course builder reads the `CycleIndex` it is
handed (standing, closed and open rounds, the newest election's `elects_on`), `derive_run_state`, the
course's own ledger spend through `scan_ledger_spend` cut at the moment, the inner task, and the
`runs_with` and `config` its caller resolved — `runs_with` once per campaign, `config` per course
through `line_config`, the line holder's shared with the bench. The campaign builder adds
`cycle_bench`, `application/runner/campaign_result.py::read_line_spend`, `LifetimeSpend` and the line.
`application/served_dashboard.py` keeps `binding_run_limits` and `fork_remainder` until stage 4.

**The tree reuses the course builder.** `infrastructure/store/lineage_queries.py::build_lineage_tree`
takes `courses: Mapping[str, HierarchyCourse]` keyed by address and folds topology only: its course entry
becomes `CourseLineage{course: HierarchyCourse, origin_row, children: list[ArmNode], lens_criterion,
lens_shift, deletable_stubs}` in `domain/lineage_tree.py` (renamed from `CourseNode` in group 1a), its
course half deleted (`run_phase`, `status`, `dataset_name`, `course_kind`, `trigger`, `fork_direction`,
`steered_by`, `task`, `run_standing`, `elects_on`; `CourseKind` becomes the `mint` tag's value set).
`application/hierarchy.py::family_course_entries` mints the family's course entries over
`infrastructure/store/lineage_queries.py::iter_family_courses` with `course_entry`, resolving
`runs_with` once per campaign and each course's `config`;
`application/mask/tree_lens.py::lensed_tree` hands them in and is the one caller.
<!-- probe: git grep -cE "build_lineage_tree\(" -- promptpotter == 2 -->
A supersede fork still folds onto its parent's timeline; the folded entry's `course` is the line
holder's entry — choosing which course to mint replaces today's rewrite of three scalars.
`?at=` on the tree mints every course at that moment, which is "the same card one layer down at a chosen
moment" for a course, served from stage 1 on.

## 4. Routes

**Added.** `GET /hierarchy?descend=&dataset=&lifecycle=&campaign=&attached=&depth=` →
`HierarchyResponse`. `descend` enters an inner store exactly as every read does today. One conditional
read: `presentation/api/routers/campaigns/_conditional.py::conditional_json` over
`(hierarchy_version(stores, inside=…), dataset, lifecycle, campaign, attached, depth)`, where
`hierarchy_version` is the campaign-list version tuple moved whole (identity, `CampaignStore.version()`
— which already stats every cycle's run-state stamps — the archive, the ruler, each dataset's
definition), which `GET /campaigns` answers on until it dies; the cycle-list version dies with its route.

**Deleted.** `GET /campaigns` (the list in `routers/campaigns/manifests.py`; the campaign-scoped
reads beside it stay) and `GET /cycles` (`routers/active.py`). `GET /active` stays: the pointer is a
tenant fact, not an entry, and its 2 s poll is its own.

**Reshaped.** `GET /campaigns/{c}/cycles/{cy}/tree` answers `CourseLineage` (§ 3); its version tuple
(`application/cycle_reads.py::tree_version`), lens and moment parameters are unchanged.

**Polling.** The registry's two 10 s reads become ONE `hierarchyRead()` on `byEtag`, every lifecycle,
so an address resolves whatever the sidebar's filter lists — the filter is layout over
`facts.lifecycle`, and the sidebar and the switcher read the filtered `listed`; the tree
poll and the active-pointer poll are untouched. Per-address ETags and `GET /hierarchy/{address}` are
stage 2's, when a course's subtree first carries rounds; stage 1's body is the campaign count.
<!-- probe: git grep -cE "hierarchyRead\(" -- webapp/lib/registry.tsx == 1 -->

## 5. Every consumer, five entry points

| Entry | Today | After |
|---|---|---|
| **CLI** | `cycles` prints `CycleListEntry` rows | `cycles` keeps its name (one of the four read verbs, `docs/operations/persistence-and-state.md` § Active session pointer) and prints one line per course entry from `list_hierarchy`: `state.status.label`, `run_phase`, `producer.label`, `pause.detail`, `admission.offers`, the `rounds` stat, `<- active pointer` on the active pointer's course; `--inside` / `--campaign` / `--attached` map to the parameters. No campaigns verb exists today and none is added — reads open the tree. |
| **`/potter-run`** | names `cycles --attached`, `cycles --inside` | unchanged — the verb and its columns survive; the skill quotes no field name. |
| **Embedded** | `application/embedded_run.py::open_session` lists nothing | unchanged — a host imports `list_hierarchy` from `application/`; parity is by placement (`<entry-point-parity>`). |
| **REST** | `/campaigns`, `/cycles`, `/tree` | § 4. `docs/specs/openapi.generated.json` regenerates. |
| **Webapp** | `/cycles` × `/campaigns` joined by `buildForest` | the table below. |

**The root-major group key is decoded, not served.** The server orders `entries` root-major
(campaigns sharing a root cycle consecutive, the group of the newest first); a header starts where the
root changes, and the root is the LEAF hop's `cycleId` of `decodeCyclePath(facts.root)`. One reader
(`ForestRows.tsx`) decodes it with the codec it already imports; a served `root_cycle_id` would be a
second spelling of a fact `facts.root` already carries, plus a served field and a ledger row.

Webapp, component by component — each reads a served entry and nothing it computed:

| Surface | Reads after stage 1 |
|---|---|
| `lib/registry.tsx` | `entries: HierarchyCampaign[]` (every lifecycle), `listed` (those the lifecycle filter shows), `byAddress: Map<string, HierarchyEntry>` over campaigns and their courses, `running: HierarchyCourse[]` (`state.producer.attached`, ordered by `webapp/lib/run-phase.ts::dockPriority`), `useEntry(address)` and `useCampaign(address)`, its campaign-level narrowing (`derivations/hierarchy.ts::campaignAt`). `useCycleEntry`, `forest`, `runningCycles` die. |
| `lib/api/reads.ts` | `hierarchyRead()`; `campaignsRead`, `cyclesRead` die. |
| `sidebar/ForestRows.tsx` | `OriginRow` groups consecutive campaigns sharing a root cycle, decoded from `facts.root` (above); `RunRow` renders a `HierarchyCampaign`; branch rows are `children` other than `facts.root` — the one filter, deleted in stage 2 when candidates move under rounds under courses and the root course becomes a row; the layout stays today's for the 10-25 demo. `CourseRow` renders a `HierarchyCourse` whether from `children` or from `CourseLineage.course` under a candidate — `innerStats` / `innerFacts` die. `CandidateRow` is unchanged (stage 2). The open set is keyed by `address` for hierarchy entries and by `nodeKeyOf` for arms. |
| `sidebar/RowHoverCard.tsx` | `SummaryBlock` takes the entry (`label`, `state.status`, `tags`, `stats`, `lede`, `caveat`); "Runs with" reads `facts.runs_with`; "Declared config" and "On disk" keep their own reads; the copy button's payload is the entry's JSON. |
| `sidebar/CampaignRowLabel.tsx` | `state.status.mark`, `tags` of kind `vendor` → `VendorLogo(value)`, `label`, headline `stats[0].value`, sub line `stats` `rounds` + `fmtAgo(span.last_at)`. |
| `sidebar/SidebarContent.tsx`, `CampaignMenu.tsx`, `AccountSpend.tsx`, `MobileAppBar.tsx`, `JobsDock.tsx` | dataset filter and dataset list from `tagValue(…, "dataset")`; auto-expand keys on `address`; the menu reads `address`, `label`, `facts.lifecycle` and seeds a rename with `facts.label`; the spend signature is `facts.spend_lifetime.billed_usd`; the app bar reads `useCampaign(campaignId)?.label` and `running.length`; the dock lists `running` with each course's `label` and its campaign's via `byAddress.get(parent)`. |
| `masthead/RunMasthead.tsx`, `CampaignSwitcher.tsx` | the badge is the `dataset` tag's label, the title `facts.label` where the operator named it, the id `shortId(address)` (served `id_suffix` dies — `shortId` already renders ids in Compare); the setup line is `facts.runs_with` + vendor tags + the `rounds` stat + `fmtAgo(span.last_at)`; the switcher groups `entries` by the dataset tag, each a campaign and its `children` with `state.status.mark`. The chip row keeps reading `dashboard.json` until stage 4. |
| `lib/workspace.tsx`, `DashboardTab.tsx`, `SteerForkPanel.tsx`, `remote/useRemoteModel.ts` | `useLeafIsL4` = `hasTag(useEntry(leaf campaign), "self_optimization")`; the viewed dataset = `tagValue(useEntry(encodeCyclePath(path)), "dataset")`; `webapp/lib/run-phase.ts::phaseWalks` over `useEntry(…)?.state.run_phase`; `state.intervened`. An inner leaf is absent from the root listing, as it is today. |
| `compare/*`, `lib/compare-selection.tsx` | `RunGroup` → `HierarchyCampaign`; the evidence re-tick key is the line entry's `address:facts.rounds_closed:state.run_phase`; `ChannelColumn` hands `SummaryBlock` the entry with `shortId(address)` prepended to its tags; `campaignCard`, `courseStats` die. |
| `lib/derivations/run-summary.ts` | keeps wording the chat's frozen run item from `dashboard.json` as `SummaryFacts` (`derivations/summary-facts.ts`, the shape `SummaryBlock` takes from an entry too), whose champion is the entry's `lede` rather than a served stat, until stage 4 reads a course entry at a moment. |
| `lib/test-fixtures.ts`, `e2e/harness.ts`, `e2e/spend/l4.spec.ts` | `hierarchyCampaign`, `hierarchyCourse`, `courseLineage` builders from the generated types; the harness reads `/hierarchy` entries (`address`, `facts.root`, the dataset tag). |

Grouping served rows for a menu or a header, filtering by a served tag and formatting a served time are
layout (`webapp/CLAUDE.md` § Scoring authority); wording a number, ordering stats or deriving a
state is not, and none survives in `webapp/lib/derivations` for these two levels.
<!-- probe: git grep -cE "buildForest|RunGroup|OriginGroup|campaignCard|courseStats|unitDisplayName|campaignLineParts" -- webapp == 0 -->
<!-- probe: git grep -cE "superseded_by" -- webapp/lib/registry.tsx == 0 -->

## 6. Docs that move with the code

`webapp/lib/CLAUDE.md` § Display-data sources gains the `/hierarchy` row and re-points its sidebar-line
rule and probe at `lib/registry.tsx`; `webapp/components/CLAUDE.md` (the masthead and switcher read
`buildForest`); `docs/specs/frontend-surface-contract.md` I6 (`producer_attached` on a `/cycles` row →
`state.producer.attached` on a course entry), I7 (the inner hop is absent from `/hierarchy`), I9
(`CampaignSummary.runs_with` → `CampaignFacts.runs_with`); `docs/operations/persistence-and-state.md`
(`GET /cycles` → `GET /hierarchy`, the verb unchanged); `docs/developer/node-standard.md` (the stale
`CampaignSummary.backend_type` → the `self_optimization` tag); `docs/operations/backend-integration.md`
(the list route); `docs/developer/adding-a-surface.md` § 9 (the worked example is `/hierarchy`);
`promptpotter/application/CLAUDE.md` § Top-level modules (`hierarchy.py` in, `campaign_listing.py` out);
`promptpotter/domain/CLAUDE.md` (its `spend.py` bullet names `LifetimeSpend` from group 1a on) and
`promptpotter/infrastructure/store/CLAUDE.md` § The lineage tree (`CourseLineage`, the course builder);
the **One served node hierarchy** bullet leaves [`roadmap.md`](roadmap.md) § Captured — pending triage
for the lane the operator names, stage 1 marked shipped, the address grammar and the fork-as-event
recorded as decided.

## 7. Deletions, additions, the ledger

**Deleted** (≈ −575 Python, ≈ −800 TypeScript):
`application/campaign_listing.py` whole; `list_cycles`, `CyclesResponse`, the cycle-list version and
`CycleListEntry`, `LineStanding`, `rounds_line`, `rounds_cap_note`, `CampaignSummary`,
`CampaignListResponse`, `total`, `campaign_metered_usd`, `id_suffix`, `comparison`, `display_name`;
`CampaignStore._list_entry` (`enumerate_cycles` yields `(CycleHop, CycleIndex)`); `_course_scalars`
and the course half of the tree's course model; the two list routes; `webapp/lib/derivations/campaign-forest.ts`,
`campaign-summary.ts` and its test, `lib/names.ts`; `useCycleEntry`, `forest`,
`runningCycles`, `innerStats`, `innerFacts`, `byDataset`'s input shape, `RunGroup`, `OriginGroup`,
`NodeKind`, `nodeKey`, `MintKind`, `LineageNode`'s course half. `campaign_list_version` goes in group
1a, its body moved whole into `hierarchy_version`.
<!-- probe: git grep -cE "class (CycleListEntry|LineStanding|CampaignSummary|CampaignListResponse|CyclesResponse)\b" -- promptpotter == 0 -->
<!-- probe: git grep -cE "def (list_cycles|cycle_list_version|campaign_list_version)\(" -- promptpotter == 0 -->

**Renamed**, each with its doc row in the same commit: `application/cycle_listing.py` →
`application/cycle_addressing.py` (what is left: the active pointer and the four resolvers the CLI
verbs share); `domain/cycle_listing.py` → `domain/cycle_index.py` (what is left: `CycleIndex`,
`IndexRound`, `RunStatus`), cited with its layer prefix wherever the projection module shares the tail;
`domain/lineage_tree.py::CourseLineage`, renamed from `CourseNode` in group 1a with the TS builder `courseLineage`.

**Added** (≈ +450 Python, ≈ +120 TypeScript): § 3's homes, `hierarchy_address`, `CourseLineage`,
the fixtures and one Vitest (§ 8). Net ≈ −800 lines, reported not pursued.

**`scripts/complexity_ledger.py::BASELINE` rows that move**, each with its written reason:
`modules` 449 → 452 after group 1a (`domain/hierarchy.py`, `application/hierarchy.py`,
`routers/hierarchy.py`) → 451 after group 3 (`campaign_listing.py` gone); `served_fields` 1642 → 1712
in group 1a (the hierarchy models, the envelope listed under both level variants) and down by the
deleted models' in group 3; `p_served` 180 → 197 in group 1a — the new entry fields have no webapp
reader until group 2, which brings it back down, and a `facts` field still unread after group 2 is
deleted there — group 2 ends at 201 (three `RunStanding` fields wait on stage 4's chip row, the
draft's two `updated_at` on group 3) and group 3 lands at 187 (`served_fields` 1653); `test_reach` 360 → 362 in group 1a (the § 3 journey imports `application/hierarchy.py`);
`test_files` 6, `test_functions` 25, `claude_md` 21, `deferred_imports` 7 unchanged.

## 8. Tests

- **Dies:** `webapp/lib/derivations/__tests__/campaign-summary.test.ts` — it asserted client wording that
  no longer exists.
- **Moves:** `tests/test_archive.py` § 3 (replayed decisions and forks) calls `lensed_tree` where it called
  `build_lineage_tree`, and reads `.course.facts.standing.selection.label` where it read
  `.run_standing.selection.label`. The `LedgerUnreadableError` reader name stays `"tree"`.
- **The new seam's assertions ride that journey, as steps, not functions** (`tests/CLAUDE.md`:
  one journey per `# N.` section; `test_functions` stays 25). Group 1a: after the supersede fork, the
  campaign entry's `facts.line` from `list_hierarchy` is the fork's address. Group 1b: the tree's folded
  `course` equals the listing's line course — the SILENT axis, a listing and a tree that disagree about
  which course holds the line would render two marks and raise nothing. A campaign entry's `state` and
  line stats are copied from its line course in one builder, so asserting them equal to that course
  re-reads one assignment and is no step.
- **Webapp:** one Vitest, `webapp/lib/derivations/__tests__/hierarchy.test.ts`, over the registry's
  `running` order and the sidebar's root grouping, from `hierarchyCampaign` / `hierarchyCourse` fixtures.
- `tests/test_security.py`'s access-log sample path becomes `/api/v1/hierarchy?campaign=camp-1`.

## 9. Builder split — four groups, one seam each, tree green after each

Every group runs `python scripts/gate.py` (the repo venv), moves the `surface-ledger` baseline it
changes with a reason, regenerates `scripts/build_ts_types.py` output and `scripts/build_specs.py`'s,
and edits the docs its seam invalidates (§ 6) in the same commit. The groups run in sequence, each in
its own worktree off the previous one's tip, reviewed read-only before the next starts; nothing reaches
`main` before the operator's "land it" — `main` serves the 2026-10-25 demo untouched.

1a. **The entry exists and is served** (pp-architect): `domain/hierarchy.py`, `hierarchy_address`,
   the runs-with and lifetime-spend models moved down (§ 3), `CycleIndex.open_rounds` /
   `elects_on`, `application/hierarchy.py` (`course_entry`, `campaign_entry`, `list_hierarchy`,
   `hierarchy_version`), `routers/hierarchy.py` mounted, `CourseNode` → `CourseLineage`, the TS regen,
   the § 8 journey step asserting the listing's line address. Nothing reads it yet; the tree and the old
   list routes are untouched.
1b. **The tree reuses the course entry** (pp-architect): `CourseLineage{course}` with the course half
   deleted, `build_lineage_tree(courses=)`, `lensed_tree` minting, `export.py`'s readers,
   `test_archive.py` § 3 extended to the tree's folded `course`, the TS regen and every webapp reader
   of the tree's course half (`candidate-views.ts`, `inner-panel.ts`, `candidates/forest-layout.ts`,
   `FitnessRankSummary.tsx`, `PanelCellRow.tsx`, `useLineage.ts`, `lineage-candidates.ts`,
   `ForestRows.tsx`'s course and inner rows, `test-fixtures.ts`, the lineage tests). Old list routes
   untouched and still read.
2. **The webapp reads the hierarchy** (pp-architect): § 5's table, the deletions under `webapp/`, the
   Vitest, the e2e harness, `webapp/lib/CLAUDE.md`, `webapp/components/CLAUDE.md`,
   `frontend-surface-contract.md`. Old list routes still served, now unread.
3. **The old listing dies** (pp-mechanic — every decision is above): the two routes, the modules and
   models of § 7, the two renames with their CLAUDE.md rows, `cmd_cycles` on `list_hierarchy`,
   `test_security.py`, the generated spec and types, the Python-side docs of § 6, `roadmap.md`, and
   this file committed — its probes hold from this commit on.

## 10. Stages 2–4 extend; nothing here reshapes

- **Stage 2 — round and candidate entries.** Two `Level` members (`HierarchyRound`,
  `HierarchyCandidate`), two facts models, two builder functions, two address segments;
  `course.children` fills; `ArmNode`, `CourseLineage` and `GET …/tree` die, and
  `nodeAddress` / `nodeKeyOf` with them; `parent` of an inner campaign refines to its candidate; per-address
  ETags and `GET /hierarchy/{address}` land so only the live course's subtree polls fast. A retained
  candidate recurring as a reference, step identity as a ledger offset, and parallel candidates as a
  sequence with a parallel mark are the roadmap's open items and belong to that stage's spec.
- **Stage 3 — step entries, spans, time lanes.** `HierarchySpan` gains ledger offsets; `spawn.at_offset` is
  already where a fork is drawn.
- **Stage 4 — the dashboard read slims to live run control.** The masthead chip row, the run card and
  `run-summary.ts` read a course entry at a moment; `ServedDashboard`'s `RunStatus.of` folds into
  `HierarchyState.of`, and the `RunStatus.of` probe above becomes `== 2`.

## 11. Open to the operator

None blocking. Two decisions taken here that the operator may reverse at no cost before group 2 lands:
the sidebar keeps today's layout in stage 1 (campaign row = its line, branch rows beneath, the root
course not a row) for the 10-25 demo, and the `cycles` verb keeps its name.

# Variation and pools — every optimizer's features as switchable members

> Read [`../architecture.md`](../architecture.md) § Bench and optimizer first. The stage order is
> [`roadmap.md`](roadmap.md) § Variation and pools; this page is the contract those stages build.

**The bench is also a laboratory for evolving prompts.** Crossover, mutation, inversion, parent
choice and survival are members any manifest composes and a researcher switches per campaign, so
"does recombination help" and "deterministic or through an LLM" are head-to-heads whose arms
differ in one knob.

## The goal

**Whatever one optimizer built, every other can use.** A member — a proposer, a sampler, an
eliminator, a selector, an analysis step that feeds another — is registered once and named by any
manifest, so optimizers are recombined as freely as the prompts they evolve. Three things follow,
and each is a stage in the roadmap:

- **A member reads a typed slice, never its runtime's whole payload.** Today a member raises under
  any manifest but its own (`optimizers/nodes.py::state_as`); that is the one thing between
  "every optimizer is a manifest" and "any manifest names any member".
- **A capability is one mechanism mounted on a host.** Recombination is not written three times
  for three nodes: one typed spec, one materializer, and a knob saying which node emits the spec.
- **The operator controls all of it from every entry point, at three moments** — configuring at
  check-in, watching a live run, steering one. § The three surfaces.

## One loop, one vocabulary

**Every optimizer is written in the loop's own three stages — generate, score, critique — and a
preset is a configuration of them, never a second framework.** Recombination is a setting of the
GENERATE stage: `l1_generate` is that stage's LLM-directed member, a crossover or a seeded draw is
another member of the same stage, and a peer's proposer is a third. CAPO, GEPA, LEVI and potter are
then four named configurations of one optimizer — the same members, some switched on and some off —
and each still reproduces its paper by the values it sets and states every departure beside the
value it departs from.

**A design choice with two defensible answers is a knob, and a preset picks its value.** What
closed access withdraws (all free text for that reader, or only the fields the pool serves), when
the steering layer hosts recombination (every round, or only on a fire), and how potter reads the
block library by default are each settable at check-in; the preset's value is the one measured to
perform, never the only one expressible.

**Settled and proposed are kept apart on this page.** § The goal and this section are settled —
why is [ADR-0007](../adr/0007-one-loop-shared-members.md). Everything below is the current
PROPOSAL for the mechanism: the stage that builds a part re-derives the shape with the fewest new
concepts first, and rewrites its section here when that shape differs.

## Vocabulary

Each word is checked against what the repo already calls things; an existing name is reused.

| Term | Means | Reuses / collides |
|---|---|---|
| **arm** | an individual a round measures | existing (`optimizers/nodes.py::Population`). This is the "active pool" — never called a pool |
| **pool** | a named set of entries of ONE kind that nodes draw from and declared writers add to | existing as `search pool`, `demo pool`, GEPA's `pool`; this page makes it one type. Never "sample pool": a sample is a dataset row |
| **entry kind** | `individual` · `block` · `shot` | `block` = `domain/prompt_block.py::PromptBlock`, a whole-field value; "allele" is not used |
| **locus** | one separately inheritable part of an individual: an open prompt field, `shot_ids`, an open `(node, param)` | `domain/opt_search_point.py::OptSearchPoint.loci`; an open locus IS an open axis |
| **variation node** | a proposer node that turns parents and pool entries into arms | "proposer" + `IndividualLineage.variations`; never bare "operator" — the operator is the human |
| **novelty** / **recombination** | a variation node's class: it can emit a value no pool held / it emits only values its inputs held | new |
| **access** | how a node reads a pool: `off` · `open` · `closed` | replaces `prompt_block_catalogue`'s `off` / `guidance` / `restrict` |
| **host** | the node a capability is mounted on | new; the node whose access to a pool is not `off` |
| **mix** | how many of a round's arms each variation node produces | new; replaces per-member counts where a manifest composes more than one |
| **preset** | a manifest whose composition and knob values reproduce a paper | existing (`roadmap.md` § The optimizer plug point) |

## The variation node contract

**A variation node is a `Proposer`** — owned by [`../developer/node-standard.md`](../developer/node-standard.md)
§ Optimizer node types; this page adds what a member declares on top.

- **Class**: `novelty` or `recombination`, a fact on the member, served. A node is `recombination`
  only if every value in its child is verbatim from a parent or a pool entry — the property
  per-locus credit rests on. An LLM merge of two texts is `novelty`.
- **Inputs it draws**: the pools it reads, by name, and for each whether its access is fixed by
  the member or a knob.
- **What it writes**: the pools it may add entries to. A node that invents text under `closed`
  readers adds it as an entry first.
- **Arity**: how many parents one child takes. The child carries every parent edge.
- **Loci**: which it may set. Under a controlled arm the open set is the prompt's own fields —
  owned by [`../architecture.md`](../architecture.md) § The controlled comparison; a variation node
  reads the open set and never widens it.
- **State**: a typed slice it needs, never a runtime's whole payload, so it runs under any manifest
  banking that slice.
- **Lineage**: each node that shaped a child is on the child, in walk order; a later node never
  overwrites an earlier one.
- **Treatment**: a deterministic member's code is in its optimizer's `Treatment` the way a prompt
  renderer's is — owned by [`../architecture.md`](../architecture.md) § Three identities.

**No member decides who survives or who mates inside a variation node.** Parent choice is the
opening node's, survival the selector's; each takes a rule knob (uniform · tournament · softmax ·
Pareto-lead · complementary; top-μ · one-to-one · archive insert). Roulette is admitted only where
the fitness is accuracy on a fixed panel — θ has no ratio scale.

## Recombination is one mechanism, and its host is a knob

- **The spec.** A recombination is a typed value: the parents by id, and for each locus it sets,
  the entry it takes. Every host emits that value and nothing else.
- **The materializer.** One deterministic function turns specs into arms through
  `OptSearchPoint.derive`, recording every parent edge and the variation step. No host builds an
  individual itself.
- **The hosts.** `l1_generate` — the proposer decides which children are produced, in the call
  that already writes its own variants. `l2_context` — the steering layer decides on a fire, and
  the opening node materializes its specs next round. An `algorithm` node — a seeded draw, no
  model. Moving the capability moves the pool panels and the reply slot to the host's dispatch;
  the mechanism does not change, so two hosts are two arms of one comparison.
- **Closed access picks by id.** The host's response schema carries the entry ids as an enum, the
  lock `model` already has — never a text match after the fact, so an invented value is
  inexpressible rather than rejected.

## The pool contract

- **Declared by the manifest**: name, entry kind, the field(s) a block pool serves, its starting
  content, its writers. The demo pool is the existing instance — owned by
  [`../architecture.md`](../architecture.md) § The bench score is not an optimizer's selection.
- **The first two pools** are the block library (`config/prompt_variants.json`) and the
  campaign's own measured individuals. Harvested templates enter later, each through a manifest
  that states its source — § Out of scope.
- **Entries are addressed by content id.** A block has `PromptBlock.id`; an individual its
  content id; a shot its sample id. A text with no id is not an entry.
- **Starting content is identity.** It is hashed into the treatment as data — the rule
  `config/prompt_blocks.py::library_identity` already applies to the block library.
- **The active subset is a knob on the pool.** A campaign names which entries are in play, by
  group — prompt field, then source, then entry — and that selection is part of identity. A pool
  is never all-on by necessity.
- **A round banks the pool it was proposed under**, by entry id, on its `optimizer_state` — owned
  by [`../../promptpotter/application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)
  § Conventions; no sidecar. A resume or a fork re-seats the pool from the round it continues.
- **Access is per reader.** `closed`: the reply is constrained to entry ids. `open`: the entries
  are shown and the value space stays free. `off`: nothing renders and the prompt is bit-identical
  to a run without the pool.
- **Static or growing is a setting.** Under `open`, a knob bounds how many of a round's arms may
  carry a new value; each new value becomes an entry with its writer recorded. At zero the pool is
  static and every value an arm carries was predefined.
- **Writers are declared and plural**: a variation node, a selector, run init (a miner), a command.
  A writer not on the list cannot add an entry.
- **A write during a run is a record, taken at a round boundary.** A node's own write is its
  decision record. A write from outside the loop is a Control-remote command — owned by
  [`../architecture.md`](../architecture.md) § Control-remote; declared in
  [`api-openapi.yaml`](api-openapi.yaml) before its handler. Editing `prompt_variants.json` or a
  manifest is not a write to a running campaign: it is a treatment change for the next mint.
- **Memory fence.** A pool fed by other campaigns is memory — owned by
  [`../architecture.md`](../architecture.md) § The controlled comparison; a controlled arm reads
  its own line's entries only.

## Knobs and the served shape

**An optimizer's knobs are its nodes' `config`** — owned by
[`../architecture.md`](../architecture.md) § Every optimizer is a manifest. This page adds no
campaign-config field and no second overlay.

- **On / off / how many** is `mix` on the opening node: `{variation node → arms}`. Zero is off. One
  knob, so a mode contrast is one edit.
- **Access** is a knob with `options` on the reading node, one per pool it reads.
- **Standing size, parent rule, survivor rule** are ordinary knobs on the opening node and the
  selector.
- **Served** on the response `GET /optimizers/{name}/knobs` already answers
  (`application/pipeline_resolve.py::OptimizerKnobsResponse`), extended: per node its class and
  the pools it draws from and writes to; the manifest's pools (name, entry kind, fields, groups,
  size, origin, writers); an object knob's own rows, so nesting is drawn from data. Declared in
  [`api-openapi.yaml`](api-openapi.yaml) first; the TypeScript is generated.
- **Refused in one place**: `optimizer_manifest.py::resolve_optimizer` — a mix naming a node the
  manifest lacks, `closed` on an empty pool, a writer not declared.

## The three surfaces

**One served shape, drawn at three moments; the terminal reaches each.** Every element is a served
field — the class tag, the pool roster, the grouping and the nesting are never derived in the
browser — owned by [`../../webapp/lib/CLAUDE.md`](../../webapp/lib/CLAUDE.md) § A wire shape is GENERATED.

- **Check-in — configuring.** Two regions, one draft: what is tested each round (the mix, as
  counts and a total) and the pools, each with its active subset and the variation nodes that draw
  from it grouped by class, each node carrying its own knobs and its access. Every control writes
  `optimization_overrides.nodes.{node}.config.{knob}` through `edit-draft-campaign` — owned by
  [`../../promptpotter/presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md)
  § No ad-hoc mutating routes.
- **Live — watching.** Each arm shows which variation node made it and from which parents and
  entries; each pool shows its size, what the round drew and what was added. A two-parent child
  draws both edges.
- **Steer — changing a run.** The mix, an access and a pool's active subset are fork-seed knobs
  like any node config, and a pool write is the command above. A controlled arm refuses both.
- **The terminal.** `--set nodes.<node>.<knob>=<yaml>`; REST and the embedded launch carry the same
  overlay; an L4 panel carries it as `inner_nodes`. **Five ways in, one overlay** — owned by root
  [`CLAUDE.md`](../../CLAUDE.md) § Working principles (`<entry-point-parity>`).
- A paper preset says which change departs from its paper, as the knob panel does today.
- Visual language: `/design`, after the wiring, and shown to the operator as a layout first.

## L4 reach

**L4 tunes whichever manifest its inner campaign selects** — owned by
[`l4-outer-loop.md`](l4-outer-loop.md); this page adds one thing it must reach.

- An outer arm reaches an inner knob only where the inner member declares it a lever
  (`optimizers/nodes.py::LlmNode.outer_levers`), and only llm nodes are in the outer's graph. `mix`
  and access become levers, so the lever declaration moves to every member and the outer graph
  carries any node declaring one.
- A lever's value space is its knob's own options and bounds; it enters the inner cell's identity
  as it resolves, so two settings never share a cell.
- **The instrument stays out of reach** — owned by [`l4-outer-loop.md`](l4-outer-loop.md)
  § Invariants; a pool that holds held-out rows, a scorer or a seed is never a lever.

## Ablation and comparability

**The guard is one served derivation** — owned by
[`../architecture.md`](../architecture.md) § The controlled comparison. What this page binds:

- **An ablation arm differs in one knob.** Variation off is `mix` at zero for that node at the same
  arm count; deterministic vs LLM recombination is the same parents and the same pool under two
  hosts. The guard names the knob.
- **`off` is bit-identical.** A node at zero and a pool at `off` leave every other node's prompt
  unchanged — the property `escalation_ladder`'s arms already have.
- **Budgets are matched twice and both are reported**: equal incurred USD and equal cells. A
  deterministic node spends no optimizer tokens, so the two answers differ.
- **A controlled arm takes no pool write.** Elsewhere a write makes the pair read `differs`,
  naming the pool.
- **Structure is a confound to control, not to claim.** A fielded individual against a one-field
  paper preset compares genomes as well as methods: run both on a one-field origin, or both on the
  fielded one.
- **The headless control.** Recombination with a fresh individual in place of the second parent,
  at equal budget, is what separates recombination from macro-mutation.
- **Which column** — owned by root [`CLAUDE.md`](../../CLAUDE.md) § The closing directive; a
  crossover child reading positive against its better parent is the promotion clock, never a
  result.

## Out of scope, deliberately

- **Field order as a locus.** `PromptTemplate.RENDER_ORDER` stays a class constant — owned by
  [`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md) § Backbone
  primitives. Inversion acts on `shot_ids`.
- **Segments inside a field.** No sentence or section model; a splitter is a new concept and
  passes the pre-flight gate on its own.
- **Lifting a population into the bench.** Individuals stay in the optimizer's payload — owned by
  [`../architecture.md`](../architecture.md) § Two-layer searchpoints + self-optimization.
- **Credit that steers.** Per-entry effects, linkage and adaptive mix are served readings first and
  decide nothing until a reading shows they are informative.
- **A learned surrogate.** Same reason.
- **Authoring a pool's content at check-in.** The check-in picks among declared entries; writing
  new ones is the pool-write command or the upload path `candidate_library` already has.
- **Param recombination in a controlled comparison.** The controlled search is the prompt's fields.
- **The harvested templates as shipped pools.** No licence was verified per source; each enters
  through a manifest that states its source and deviations, or not at all.

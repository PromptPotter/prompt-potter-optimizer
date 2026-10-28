# Variation and pools — every optimizer's features as switchable members

> Read [`../architecture.md`](../architecture.md) § Bench and optimizer first. The stage order is
> [`roadmap.md`](roadmap.md) § Variation and pools; why one loop with shared members is
> [`decisions.md` § 0007](../decisions.md#0007--one-loop-shared-members). Below § Vocabulary this page
> is the current PROPOSAL; the stage that builds a part re-derives it with the fewest new concepts.

**Whatever one optimizer built, every other can use.** Crossover, mutation, inversion, parent choice
and survival are members registered once and named by any manifest, so "does recombination help" and
"deterministic or through an LLM" are head-to-heads whose arms differ in one knob.

- **A capability is one mechanism mounted on a host**: one typed spec, one materializer, and a knob
  saying which node emits the spec.
- **The operator controls it from every entry point** at check-in, live and by steer (§ The three surfaces).
- **A preset is a configuration of generate · score · critique, never a second framework.** CAPO, GEPA,
  LEVI and potter are named configurations of one optimizer; each reproduces its paper by the values it
  sets and states every departure beside the value.
- **A design choice with two defensible answers is a knob, and a preset picks its value** — what closed
  access withdraws, when the steering layer hosts recombination, how potter reads the block library.

## Vocabulary

| Term | Means | Reuses / collides |
|---|---|---|
| **arm** | an individual a round measures | `domain/optimizer_state.py::OptimizerState.population` — the "active pool", never called a pool |
| **pool** | a named set of entries of ONE kind that nodes draw from and declared writers add to | `domain/pool.py::Pool` (`BlockPool`, `DemoPool`); never "sample pool" |
| **entry kind** | `individual` · `block` · `shot` | `block` = `domain/prompt_block.py::PromptBlock`; "allele" is not used |
| **locus** | one separately inheritable part: a `Locus(node, key)` — an open prompt field, a node's shots, an open config param | `domain/opt_search_point.py::OptSearchPoint.loci` |
| **variation node** | a proposer node turning parents and pool entries into arms | `IndividualLineage.variations`; never bare "operator" — the operator is the human |
| **novelty** / **recombination** | can emit a value no pool held / emits only values its inputs held | new |
| **access** | how a node reads a pool: `off` · `open` · `closed` | `domain/pool.py::Access`; potter's `l1_generate.block_access` |
| **host** | the node a capability is mounted on | new |
| **mix** | how many of a round's arms each variation node produces | new |
| **preset** | a manifest reproducing a paper | `roadmap.md` § The bench (M13) |

## The variation node contract

**A variation node is a `Proposer`** — owned by [`../developer/node-standard.md`](../developer/node-standard.md)
§ Optimizer node types; a member declares on top:

- **Class**, served: `recombination` only if every value in its child is verbatim from a parent or a pool
  entry — the property per-locus credit rests on. An LLM merge of two texts is `novelty`.
- **Inputs** (pools by name, access fixed or a knob), **writes** (pools it may add to; text invented under
  `closed` readers becomes an entry first), **arity** (the child carries every parent edge).
- **Loci**: it reads the open set and never widens it — owned by [`../architecture.md`](../architecture.md) § The controlled comparison.
- **Lineage**: every node that shaped a child is on it, in walk order, never overwritten.
- **Treatment**: every roster member's module, and every module shaping its prompts, is in its optimizer's `Treatment` (`optimizers/nodes.py::source_digest`) — owned by [`../architecture.md`](../architecture.md) § Three identities.

**Parent choice is the opening node's, survival the selector's** — never a variation node's; each takes a
rule knob (uniform · tournament · softmax · Pareto-lead · complementary; top-μ · one-to-one · archive insert).
Roulette only where fitness is accuracy on a fixed panel — θ has no ratio scale.

## Recombination is one mechanism, and its host is a knob

- **The spec**: the parents by id and, per locus set, the entry taken. Every host emits that and nothing else.
- **The materializer**: one deterministic function turns specs into arms through `OptSearchPoint.derive`,
  recording every edge and the variation step. No host builds an individual.
- **The hosts**: `l1_generate` (in the call that writes its variants), `l2_context` (on a fire; the opening
  node materializes next round), an `algorithm` node (a seeded draw). Moving the host moves the pool panels and
  the reply slot; two hosts are two arms of one comparison.
- **Closed access picks by id**: the host's response schema carries entry ids as an enum, so an invented
  value is inexpressible rather than rejected.

## The pool contract

**A pool's shape, sources, access and banking** — owned by
[`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md) § Other surfaces; still to build:

- **The campaign's own measured individuals are the next pool**, each entry its content id.
- **Static or growing is a setting**: under `open`, a knob bounds how many arms may carry a new value, each
  recorded with its writer; at zero the pool is static.
- **Writers are declared and plural** (a variation node, a selector, a run-init miner, a command). The arm
  gate's seat draws what the proposers it stands in for draw.
- **A write during a run is a record at a round boundary**: a node's own write is its decision record; an
  outside write is a Control-remote command, payload declared before handler
  ([`../architecture.md`](../architecture.md) § Control-remote). Editing a pack or manifest is a treatment
  change for the next mint.

## Knobs

**An optimizer's knobs are its nodes' `config`** — owned by [`../architecture.md`](../architecture.md)
§ Every optimizer is a manifest; no campaign-config field, no second overlay. `mix` on the opening node is
`{variation node → arms}`, zero is off; standing size, parent rule and survivor rule are knobs on the opening
node and the selector.

## The three surfaces

**One served shape, drawn at three moments** — class tag, pool roster, grouping and nesting are served, never
derived in the browser ([`../../webapp/lib/CLAUDE.md`](../../webapp/lib/CLAUDE.md) § A wire shape is GENERATED).

- **Check-in**: the mix (counts and total) and the pools, each with its active subset and its drawing nodes
  grouped by class with their knobs and access; every control writes
  `optimization_overrides.nodes.{node}.config.{knob}` through `edit-draft-campaign`
  ([`../../promptpotter/presentation/CLAUDE.md`](../../promptpotter/presentation/CLAUDE.md) § No ad-hoc mutating routes).
- **Live**: each arm shows its variation node, parents and entries; each pool its size, draw and additions.
- **Steer**: mix, access and active subset are fork-seed knobs; a pool write is the command above. A
  controlled arm refuses both.
- **Terminal**: `--set nodes.<node>.<knob>=<yaml>`; REST and the embedded launch carry the same overlay, an
  L4 panel carries it as `inner_nodes` — root [`CLAUDE.md`](../../CLAUDE.md) `<entry-point-parity>`.

## L4 reach

**L4 tunes whichever manifest its inner campaign selects** — owned by [`l4-outer-loop.md`](l4-outer-loop.md).
An outer arm reaches an inner knob only where the member declares it a lever
(`application/optimizers/nodes.py::LlmNode.outer_levers`); `mix` and access become levers, so the declaration
moves to every member. A lever enters the inner cell's identity as it resolves. **The instrument stays out of
reach** ([`l4-outer-loop.md`](l4-outer-loop.md) § Invariants): a pool holding held-out rows, a scorer or a
seed is never a lever.

## Ablation and comparability

**The guard is one served derivation** — owned by [`../architecture.md`](../architecture.md) § The controlled comparison.

- **An ablation arm differs in one knob**, and the guard names it: `mix` zero at the same arm count;
  deterministic vs LLM recombination is the same parents and pool under two hosts.
- **`off` is bit-identical**: every other node's prompt unchanged.
- **Budgets are matched twice and both reported**: equal USD and equal cells.
- **A controlled arm takes no pool write**; elsewhere a write makes the pair read `differs`, naming the pool.
- **Structure is a confound to control**: a fielded individual against a one-field preset runs both on one
  origin shape.
- **The headless control** — a fresh individual as second parent at equal budget — separates recombination
  from macro-mutation.
- A crossover child positive against its better parent is the promotion clock, never a result (root
  [`CLAUDE.md`](../../CLAUDE.md) § The closing directive).

## Out of scope, deliberately

- **Field order as a locus** — a node's declaration ([`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md)
  § Backbone primitives); inversion acts on shots.
- **Segments inside a field** — a splitter is a new concept and passes the pre-flight gate on its own.
- **Lifting a population into the bench** — individuals stay in the optimizer's payload.
- **Credit that steers, a learned surrogate** — served readings first, deciding nothing until shown informative.
- **Authoring pool content at check-in** — the check-in picks; writing is the pool-write command or `candidate_library`'s upload.
- **Param recombination in a controlled comparison.**
- **A shipped block library** — a block pool's material is the packs its `packs` knob names, installed by the
  operator, each cited work carrying its licence.

# Variation and pools — every optimizer's features as switchable members

> Read [`../architecture.md`](../architecture.md) § Bench and optimizer and [`../decisions.md` § 0007](../decisions.md#0007--one-loop-shared-members)
> first; the stage order is [`roadmap.md`](roadmap.md) § Variation and pools. What is built is the
> code's — `domain/pool.py::Pool`, `application/optimizers/variation/members.py` (`Mating`, `LocusCrossover`,
> `Survival`), `domain/opt_search_point.py::taken`, `pipeline_responses.py::OptimizerSetup`. This page is
> what is still open; the stage that builds a part re-derives it with the fewest new concepts.

## Vocabulary

| Term | Means | Reuses / collides |
|---|---|---|
| **arm** | an individual a round measures | `OptimizerState.population` is the "active pool", never called a pool |
| **pool** | a named set of entries of ONE kind that nodes draw from and declared writers add to | `domain/pool.py::Pool`; never "sample pool" |
| **entry kind** | `individual` · `block` · `shot` | "allele" is not used |
| **locus** | one separately inheritable part | `domain/opt_search_point.py::Locus` |
| **variation node** | a proposer turning parents and pool entries into arms | never bare "operator" — the operator is the human |
| **novelty** / **recombination** | can emit a value no pool held / emits only values its inputs held | served class |
| **access** | how a node reads a pool: `off` · `open` · `closed` | `domain/pool.py::Access` |
| **host** | the node a capability is mounted on | `l1_generate`'s `recombination_host` |
| **mix** | how many of a round's arms each variation node produces | unbuilt |
| **preset** | a manifest reproducing a paper | `roadmap.md` § The bench (M13) |

## Still to build

- **`mix` on the opening node**: `{variation node → arms}`, zero is off; a knob, so an ablation arm
  differs in one knob and the guard names it.
- **Parent and survivor rules beyond `tournament`**: uniform · softmax · Pareto-lead · complementary on
  `Mating`; top-μ · one-to-one · archive insert on `Survival`. Roulette only where fitness is accuracy on
  a fixed panel — θ has no ratio scale. A parent is ranked on θ alone (`node_context.py::Lineage.theta`):
  arms of different rounds walked different cells, so their accuracies never rank.
- **Static or growing is a setting**: under `open`, a knob bounds how many arms may carry a new value,
  each recorded with its writer; at zero the pool is static.
- **Writers are declared and plural** (a variation node, a selector, a run-init miner, a command). A write
  during a run is a record at a round boundary: a node's own write is its decision record; an outside
  write is a Control-remote command, payload declared before handler
  ([`../architecture.md`](../architecture.md) § Control-remote). Editing a pack or manifest is a
  treatment change for the next mint. A controlled arm takes no pool write; elsewhere a write makes the
  pair read `differs`, naming the pool.
- **Live surface**: each pool shows its size, draw and additions beside the lineage the subtree already
  serves (`descends_from`, `donor`, `donated_loci`, `gene_sources`).
- **L4 reach**: `mix` and access become levers (`application/optimizers/nodes.py::LlmNode.outer_levers`), so the declaration
  moves to every member. The instrument stays out of reach ([`l4-outer-loop.md`](l4-outer-loop.md)
  § Invariants): a pool holding held-out rows, a scorer or a seed is never a lever.
- **The headless control** — a fresh individual as donor at equal budget — separates recombination
  from macro-mutation. Budgets are matched twice and both reported: equal USD and equal cells. A
  fielded individual against a one-field preset runs both on one origin shape. A crossover child
  positive against its better parent is the promotion clock, never a result
  ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md) § Reading a round).

## Out of scope, deliberately

- **Field order as a locus** — a node's declaration; inversion acts on shots.
- **Segments inside a field** — a splitter is a new concept and passes the pre-flight gate on its own.
- **Lifting a population into the bench** — individuals stay in the optimizer's payload.
- **Credit that steers, a learned surrogate** — served readings first, deciding nothing until shown informative.
- **Authoring pool content at check-in** — the check-in picks; writing is the pool-write command or `candidate_library`'s upload.
- **Param recombination in a controlled comparison.**
- **A shipped block library** — a block pool's material is the packs its `packs` knob names, installed by
  the operator, each cited work carrying its licence, or written by the run's account.

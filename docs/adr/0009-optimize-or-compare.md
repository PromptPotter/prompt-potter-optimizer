---
status: accepted
date: 2026-10-21
deciders: [maintainer]
consulted: []
informed: []
relates:
  - docs/architecture.md
  - docs/methods/verdict-resolution.md
supersedes: []
superseded-by: []
tags: [measurement, reproducibility, head-to-head, memory]
---

# Two ways to run: optimize, or compare under control

## Context and Problem Statement

PromptPotter serves two purposes that pull in nearly opposite directions.

- **Optimize.** The operator wants the best configuration, and anything that helps is admissible:
  the tenant's whole archive as memory, a δ ruler fit over every configuration ever measured on the
  dataset, cross-run mutation memory, the block library, a steer.
- **Compare.** The operator studies one optimizer or several, and a number counts only if a fresh
  run reproduces it. Whatever an arm holds only because it sits on this disk is a confound, even
  where it would make the optimizer better.

`docs/architecture.md` § The controlled comparison names the switch (`Session.purpose`) and
lists what it withholds. It does not say what a mechanism does once its input is withheld, and a
mechanism left to its own devices fits itself on what remains: the arms under test. A fenced δ
ruler fit on the origin's own rows reproduces the origin's hit pattern, the panel selector then
draws the origin's known hits, and the θ gate favours the parent every round — a comparison that
is invalid and says nothing.

## Decision

1. **The purpose is declared on the run and read through one predicate**, never inferred from
   what happens to be on disk. It is `RunDeclaration.purpose`, `Optimize | Compare(arm)`: a run
   compares exactly when it is declared as an arm of a head-to-head, the arm rides the purpose, and
   no second field restates it.
2. **Every mechanism that reads past the run's own declaration states its behaviour under both
   purposes, beside its code.** One with no stated controlled behaviour is a defect.
3. **Fencing withholds; it never biases.** Under control, a withheld input is replaced by an input
   paid inside the declaration identically for every arm, or by the mechanism's cold form — never
   by an estimate from the arms under test.
4. **A quantity that judges a race is never estimated from that race**, under either purpose. Optimize
   admits more data; it does not admit the parent's own rows into the scale its children are
   judged on.
5. **The cache is not memory.** Replaying a paid cell by its content is the same measurement,
   priced, and is admitted under both purposes.
6. **Every reading carries the purpose it came from**, and a controlled and an uncontrolled reading
   never share a column.

## Consequences

- A memory read goes through the run's scope (`archive_queries.py::bind_memory_scope`), and the δ
  ruler a race is judged on leaves out the rows of the individuals it judges
  (`bench/difficulty.py::judging_ruler`).
- A head-to-head result from a mechanism that violated decision 3 or 4 is not a comparison.
- The optimize purpose can grow richer without touching what a controlled arm reads.

## Rejected

- **One purpose, always isolated.** It throws away paid measurement that optimizing wants.
- **One purpose, always shared.** No reproducible number exists.
- **Fencing by subtraction alone.** It degrades a mechanism silently into a biased one.

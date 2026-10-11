---
status: accepted
date: 2026-10-11
deciders: [maintainer]
consulted: []
informed: []
relates:
  - docs/specs/variation-and-pools.md
  - docs/specs/roadmap.md
supersedes: []
superseded-by: []
tags: [optimizers, manifests, members, pools, variation]
---

# One loop, shared members: an optimizer is a configuration, never a package of its own

## Context and Problem Statement

Every optimizer here is a manifest the bench runs, and that was meant to make them comparable
and composable. It made them comparable. It did not make them composable: each preset's members
read their own runtime's whole state, so a manifest can NAME another optimizer's member and the
round dies when that member runs. Four sealed packages share a bench and nothing else, and each
new idea — a crossover, a pool of reusable prompt material, a different survivor rule — would be
built a fifth time inside whichever package wanted it first.

The operator's research question is the comparison BETWEEN such features under one instrument:
does recombination help, by arithmetic or through a model, decided by which node. That question
cannot be asked of sealed packages.

## Decision

1. **One loop.** Every optimizer is written in three stages — generate, score, critique. A paper
   preset is a named configuration of that loop: the same members, some on and some off, with the
   values its paper states. It is never a second framework.
2. **A member is shared.** It declares the typed slice of state it reads, so any manifest may
   name any registered member. What one optimizer built, every other can use.
3. **A capability is built once, and which node exercises it is a setting.** Recombination decided
   by the proposer, by the steering layer or by a rule with no model is one implementation under
   three settings, never three implementations.
4. **What variation draws from is a pool the operator can see and shape** — which entries are in
   play, whether it is fixed or grows, and who may add to it. A value on a child is traceable to
   the entry it came from.
5. **This record fixes the outcome, not the mechanism.** How a pool is held and addressed is the
   building stage's to derive, by the fewest new concepts.
6. **A design choice with two defensible answers is a knob, and a preset picks its value.** The
   preset's value is the one measured to perform, never the only one expressible.
7. **The operator controls all of it from every entry point**, at check-in, on a live run, and by
   steer; each control writes the one node overlay.

## Consequences

- A change that adds a feature to one optimizer's package alone is the defect this decision names.
  The feature is a member, registered once.
- A paper preset still reproduces its paper, by the values it sets, and states each departure.
- Identity moves whenever a shared member's code or a pool's starting content moves; that
  re-measure is the price and is expected.
- The contract every stage builds against is `docs/specs/variation-and-pools.md`, deleted when it
  ships. This record stays.

## Rejected

- **A composed GA as a fifth manifest with its own members.** It answers the first question and
  repeats the sealing for the next one.
- **Settling each open choice by fiat.** A fixed answer removes the comparison the bench exists to
  run.

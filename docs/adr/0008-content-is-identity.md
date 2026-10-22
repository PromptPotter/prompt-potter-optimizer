---
status: accepted
date: 2026-10-21
deciders: [maintainer]
consulted: [architecture-review-2026-10-19]
informed: []
relates:
  - docs/architecture.md
  - docs/adr/0002-identity-foundation.md
  - docs/adr/0003-spend-and-tenancy.md
  - docs/adr/0007-one-loop-shared-members.md
  - docs/adr/0009-optimize-or-compare.md
  - docs/specs/variation-and-pools.md
supersedes: []
superseded-by: []
tags: [identity, datasets, individual, optimizer-state, budget, round, on-disk-shape]
---

# One wipe, seven shapes: content is identity, a name is a label

## Context and Problem Statement

The bench's backbone grew one consumer at a time, and its core nouns each had more than one
spelling. A dataset was a name in one place and a content hash in another, so a rename was
three rewrites. An individual was a fixed field set on whichever prompt node came first, so an
agent with two prompts could not be stated. An optimizer's state was one payload per manifest,
so a member reading it died under any other manifest. A campaign, an L4 seat and a head-to-head
arm each declared "what runs, on what, at what budget" in a shape of their own. A limit had
several types, and a round stored its headline beside the facts that decide it.

Each re-shape changes what a ledger record, a round file or a cache holds. Landed one at a time,
each would pay its own wipe or its own migration, and both are bridges.

## Decision

The re-shapes land as ONE breaking arc with one workspace wipe.

1. **A dataset is its content; its name is a label.** Its identity is
   `shared/hashing.py::dataset_hash`, over each row as its bank stores it, in order, so a reordered
   or re-cut bank is a new bank. A tenant maps labels onto hashes, and a bank is held once by its
   hash. A measured answer is filed under `(config_key, sample_key)` and the dataset hash; no key
   holds a row's position or a dataset's name. A rename moves a label and rewrites no row.
2. **An individual is `{locus: value}` over the roster its pipeline declares.** A locus is
   `Locus(node, key)`: a prompt node's fields, its shots, a node's config params. Each prompt node
   declares its own fields in render order, and a prompt node declaring none is refused at parse:
   no field set is one every individual has. Shots are sample keys. The id is the content.
3. **Optimizer state is a map of typed parts.** `optimizer_state` is
   `{manifest, population, prompt_hashes, slices, outputs}`; each part is a registered `StatePart`,
   carried across rounds (`StateSlice`) or produced by one (`RoundOutput`). A member declares what
   it reads and writes, and a roster leaving a part open is refused at load. A pool is a slice
   kind. The manifest is read through one typed model (`PipelineManifest`).
4. **One run declaration.** `domain/campaign.py::RunDeclaration` holds only the keys a layer set:
   dataset hash, run seed, loop, budget, environment (connector and target), purpose, and the pin
   of the bench defaults it is a delta over. `campaign_config.py::effective_config` is the one
   derivation of what it runs. Every door declares through `jobs/mint.py::declare`, an L4 seat at
   its spawn included. A head-to-head stores the part its arms share (`SharedPart`). A check-in is
   the account's draft of a declaration, and Start mints the campaign from it, so the root cycle
   id is content-addressed from the first record.
5. **One `Budget`, at four positions.** `domain/spend.py::Budget` bounds USD, tokens, rounds and
   wall clock. It is asked by a declaration or a launch, admitted on the job, standing as the
   operator's last move, and spent as a fold of the spend facts. The money ceiling is the
   campaign's: one book its live cycles share. A per-send bound (`LLMSpendBound`,
   `WebSpendBound`) prices one hold and is not a run budget.
6. **A round is an envelope, and its headline is derived.** `RoundOutcome` holds the walk, the arms
   by `ArmKey` as an `ArmState` sum (`Proposed | Measuring | Cut | Measured`, each with only the
   fields its stage has), the row address of every cell, the `Election` and `optimizer_state`. The
   headline arm, its level and `improved` are read off the election and the rows, never stored
   beside them.
7. **Cross-tenant measurement sharing is a policy, refused by default.** The content-addressed
   caches root at an archive home (`layout.py::archive_home`): the tenant, unless the install names
   a shared one (`settings.SHARED_ARCHIVE`). What ADR-0002 says about it is its § Contract B. A
   store keyed by what one account holds or decided — dataset labels, the δ ruler — stays the
   tenant's. Decision 1 is what makes a shared home safe: a row names a hash, never a label.

A model's default host is resolved into the node config before the key is taken
(`domain/pipeline_overlay.py::pin_default_routes`, from `registry.py::_MODEL_PROFILES`), so every
cell names its host. It re-keys every cell, which is why it rides this wipe. The pin is a trial: it
stays only where the realized cache share is measured cheaper than running uncached.

## Consequences

- The wipe re-keys everything: cells, configuration keys, root cycle ids, rounds and the ledger
  records above. Nothing on disk is migrated; the seed-screen sidecars are kept as data, and a
  recompute over them buys its own rows.
- A rename, re-point, restamp or re-freeze verb has nothing left to do. A new one appearing is the
  defect this record names.
- A new optimizer adds part kinds, a new round shape adds a walk, and a new target kind adds a
  locus kind. None adds an envelope.

## Rejected

- **One arc per shape.** Each re-keys the archive or the ledger alone, so separate arcs pay
  several wipes or write migrations.
- **Name as identity plus a hash guard.** Its cost is a re-point function per store holding the
  name.
- **A second field set for a second prompt node.** That is a second individual type beside the
  first.
- **A shared archive home as the default.** A hit tells one account what another ran, and under
  BYO keys it serves one account's paid cell to another for free.
- **Folding the per-send bounds into `Budget`.** They bound one send; a run budget bounds the sum.

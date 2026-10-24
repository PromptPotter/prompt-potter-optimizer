# paper/ — the M14 manuscript

Why this is not under `docs/`: that tree describes the system to someone working on it, and a page
that argues about a *field* has no owner there. `docs/research/related-work.md` proves the point —
it was folded into `methods/candidate-elimination.md`, which kept the bandit-family half and
dropped the peer roster. This directory exists so that does not repeat.

**What M14 is, and its rules — owned by the root [`CLAUDE.md`](../CLAUDE.md) § The closing
directive.** Read it there. **The subject changed when M13 became the bench:** the contribution is
that the stopping rule and the comparability guard are *executable* here and that every peer ran
under them. Pricing a lift in wall clock and dollars is the evidence for that, no longer the claim.

The three files under `related/` are recovered git blobs, and their provenance is the only fact
this page owns:

| File | Recovered from | Why it is not in `docs/` any more |
|---|---|---|
| `related/related-work.md` | `git show d5f5f7e0^` | folded away; only its bandit half survived |
| `related/algorithm-configuration-lineage.md` | `git show 69d170d1^` | deleted in a consolidation pass |
| `related/pevol-bench.md` | `git show c0ac0c88^` | superseded by `docs/research/benchmarks.md` § PEvol-Bench, which is shorter and current |

`evidence/` holds run output a claim rests on, kept as the run wrote it: each arm's console log
from the four-optimizer head-to-head of 2026-09-28, and the EP 04 wall-clock breakdown. The
head-to-head logs are a placeholder — the first run after the bugs they surfaced are fixed
replaces them. `notes/ep04-findings.md` is the record of what EP 04 surfaced on its day, not
current state.

Method sections stay where the code is: `docs/methods/` and `docs/research/benchmarks.md`. Cite
them; copying one here is what makes two of them.

## Decisions taken

1. **The model changes once, deliberately.** Development stays on a cheap flash model; the paper
   arm may run a dearer one. A model change re-keys the archive, so never mid-matrix.
2. **Setup instrumentation is a separate project** — an agent that stands the rig up for a named
   task from nothing, run as a Harbor agent so its own episode is a measured cell. The claim here
   scopes to ledger-open → first improvement, fully decomposed, and says so.
3. **The HITL arm ships in the paper**, matched on evidence and on wall clock.
4. **Unattended runs stay on the operator's machine**; ask before each whether to move them to the
   box. An ordinary campaign cannot yet report time the machine slept.
5. **The algorithm-configuration lineage leads; the cost axis is its evidence.** The title is
   about configuration under a noisy, expensive objective, not about prompts, and the PEvol-Bench
   definition and the screening roster are sections, not appendices.
6. **The harbor step schema is two terms, skill-used × verifier-passed**, declared before the first
   paid cell, because the fingerprint folds the term → judge mapping. It separates "the skill was
   ignored" from "the skill was wrong"; both rubrics are screened before the matrix funds them.

## The sequence

Stages close on a verification. **If something has to give, the benchmark count gives, never the
rigor** — two benchmarks with a held-out split are citable, five with selection-biased numbers are
the gap this paper writes about. M13's comparability run and budget dimension
([`../docs/specs/roadmap.md`](../docs/specs/roadmap.md) § The optimizer plug point, stages 5–6) are
the matrix; what follows is what the paper needs beside them.

1. **The unlock — nothing runs unattended before it.** The per-cell wall-clock and spend envelope and
   the determinism clamp are still L4-private (`application/runner/inner/spawn.py`,
   `application/bench/llm_call.py`); lift both to every entry point. Declare the step schema
   (decision 6). The first unattended run is `noise-floor --k 3` on each wired harbor dataset, and
   it also fills the three harbor `campaign.yaml` `per_cell` cost anchors, which the local census
   could not.
2. **The comparison made publishable.** The grader asymmetry (`extract_last_bold` on our side only)
   is one edit; then fix the inference-budget policy — the spend axes pinned, or compute reported
   as a dimension. Throughput: a 10-round harbor campaign at ~31 s a cell is ~5.5 h; `max_turns`
   4 → 3 and look-ahead 2 are the free levers.
3. **A round made readable, then the bar.** Re-read separability on the shared ruler before
   tightening the bar; until then the headline is time to the first SEPARABLE round. The COLLAPSED
   gate is blind on every verifier-graded backend. Write the pre-registration (arms, factors,
   replicates, stopping rule, primary metric, the null-result framing) here, before the freeze.
4. **The budget axis, then the freeze.** Serve time-to-first-improvement and the phase
   decomposition off the ledger. Everything measured after the engine freezes enters the results
   table; everything before is a pilot and is labelled one.
5. **Selection-clean reporting**: the held-out partition, and the author on `SubjectReading`, which
   unlocks the HITL read.
6. **HITL as an arm** — the paired-fork protocol, every round read on the shared-cell `overlap`
   line.
7. **Ablations, peers, baselines.** Free first, off banked data: the statistical-layer ablations
   (ε, `elimination_n_min`, the √φ correction, the sign-posterior bound), the negative-results
   roster, published lifts against our noise floors. Then paid: `escalation_ladder` `l1` / `l1_l2`
   / `full`; random search and best-of-N at matched wall clock; our lift on the peers' models
   beside their published numbers, never in a shared cell; HITL against auto; amortized
   break-even per arm; transfer of an evolved prompt across models.
8. **Write** — the citation filter below, run at the outline. The full spec review M13 owes comes
   after stage 4, never beside a measurement programme.

## The citation filter — run it at the outline, not at the end

A reference is admitted by **a claim the manuscript actually makes**, never by topical relevance.
The roster is roughly fifteen, most of it already spoken for by ancestry and by the stopping-rule
/ comparability-guard spine, so the only question a candidate answers is *which sentence of ours
stops being defensible without it* — and when that sentence belongs to a section we decide not to
write, the reference leaves with the section. The end-of-draft citation pass **verifies** the
roster; it must not select it, because by then every entry is attached to prose written around it.

Four verdicts the filter produces, each named by what would have to change to move a paper out of
it:

- **Forced** — the claim is unavoidable. `p1` ([2604.08801](https://arxiv.org/abs/2604.08801)) the
  moment a round buys cells adaptively at all (`build_round_order`, cut by PoBB); Winner's Curse
  ([2605.05973](https://arxiv.org/abs/2605.05973)) for any number the same data selected — every
  headline until the held-out partition, and the reason the partition exists after it.
- **Bought by work that has not landed** — *Optimization before Evaluation*
  ([2604.27637](https://arxiv.org/abs/2604.27637)) is required only once an unoptimized-origin
  baseline exists for the peers. `shared_config.py::export_results` has no origin field, so the
  criticism is not ours to make today.
- **Pick one** — the fragmentation claim takes exactly one taxonomy anchor. Citing both surveys is
  the tell that the frame is not settled. **The anchor choice is now live rather than settled**:
  with the bench as the contribution, the paper sits on the recursive-self-improvement axis as much
  as the prompt-optimization one, and a taxonomy that indexes harness self-improvement may serve the
  claim better than a prompt-optimization survey. Decide it at the outline, not after.
- **Rides a section** — AutoDesign under the harness-evolution grouping, or as an exhibit in the
  hygiene argument on the strength of its single baseline; HybridFlow only if the
  harness-vs-weights dial becomes a section *and* is priced, which needs a run nobody has made.

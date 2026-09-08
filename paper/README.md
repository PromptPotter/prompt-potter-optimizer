# paper/ — the M13 manuscript

Why this is not under `docs/`: that tree describes the system to someone working on it, and a page
that argues about a *field* has no owner there. `docs/research/related-work.md` proves the point —
it was folded into `methods/candidate-elimination.md`, which kept the bandit-family half and
dropped the peer roster. This directory exists so that does not repeat.

**What M13 is, and its rules — owned by the root [`CLAUDE.md`](../CLAUDE.md) § The closing
directive.** Read it there.

The three files under `related/` are recovered git blobs, and their provenance is the only fact
this page owns:

| File | Recovered from | Why it is not in `docs/` any more |
|---|---|---|
| `related/related-work.md` | `git show d5f5f7e0^` | folded away; only its bandit half survived |
| `related/algorithm-configuration-lineage.md` | `git show 69d170d1^` | deleted in a consolidation pass |
| `related/pevol-bench.md` | `git show c0ac0c88^` | superseded by `docs/research/benchmarks.md` § PEvol-Bench, which is shorter and current |

Method sections stay where the code is: `docs/methods/` and `docs/research/benchmarks.md`. Cite
them; copying one here is what makes two of them.

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
  moment a round buys cells adaptively at all (`select_round_subset`); Winner's Curse
  ([2605.05973](https://arxiv.org/abs/2605.05973)) for any number the same data selected — every
  headline until the held-out partition, and the reason the partition exists after it.
- **Bought by work that has not landed** — *Optimization before Evaluation*
  ([2604.27637](https://arxiv.org/abs/2604.27637)) is required only once an unoptimized-origin
  baseline exists for the peers. `shared_config.py::export_results` has no origin field, so the
  criticism is not ours to make today.
- **Pick one** — the fragmentation claim takes exactly one taxonomy anchor. Citing both surveys is
  the tell that the frame is not settled.
- **Rides a section** — AutoDesign under the harness-evolution grouping, or as an exhibit in the
  hygiene argument on the strength of its single baseline; HybridFlow only if the
  harness-vs-weights dial becomes a section *and* is priced, which needs a run nobody has made.

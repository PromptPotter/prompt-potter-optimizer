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

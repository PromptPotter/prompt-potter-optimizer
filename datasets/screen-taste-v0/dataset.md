# Screen Taste (v0)

The demo dataset: **ten things you like → an ordering of ten candidates.** Chosen because the input
is something almost anyone can type from memory, it needs no account on any platform, and a list of
films sits below the sensitivity line the consent gate draws — so it can be shown live.

Films and series share one pool. The name says *screen* rather than *movie* for that reason: the
person keeps one list, and splitting it would have thrown away the pairs that cross the line, which
are the most legible hits a recommender can land.

## Why a slate, and not free recall

The first cut of this dataset asked the model to *name* the held-out title, and its origin scored
**0.000 on all 20 cells** — a floor constant, so no later round could have moved. Naming one exact
title out of all cinema is something a good recommender fails too, so the metric was measuring the
task's impossibility rather than the prompt.

A slate makes the target reachable, and — the part free recall had no version of — gives the metric
a known **chance value of 0.2929** (mean reciprocal rank over ten shuffled candidates). A round at
0.29 read the person no better than a coin did, which is a fact no accuracy column states on its
own. Those 20 measurements were deleted rather than preserved under a new name: they cost $0.001,
the finding is written here, and a stale cell keyed to this name would have been served to the new
rows silently.

**The slate cut did not clear the floor either, and this name is spent.** Its origin read
**0.2004** against the 0.2929 chance value above — below it, not merely near it — so the exhibit
has no dataset yet and nothing here is a working demo. What ships under this name is the STUB: the
two title pools, the builder, and the config entry. The 20 rows were thrown away with the
measurements, for the reason in the paragraph above. The next cut is a **new name**, and the first
thing to compute on it is the chance floor — before a single round is paid for.

## Type

Single `llm_only` generation node, no retrieval. Reads the liked titles and the slate, returns the
slate reordered, against a running TermNorm backend.

## Data — provenance & cut

- 20 rows, cut by `build_rows.py` from two pools: `titles.txt` (this person's own list, hand-kept)
  and `distractors.txt` (widely-known titles they did not list). One title per line, canonical
  English. A title on both lists makes a row unscoreable, so the builder refuses to run on one.
- Each row is one **draw**: eleven liked titles sampled, ten shown as context and the eleventh
  hidden in a shuffled slate beside nine distractors. Rows are deduplicated on the held-out title.
- `SEED` in the builder fixes the cut, so a re-run reproduces it exactly.
- **The liked pool is thin (30).** Every title appears in many of the 20 rows, so the rows overlap
  heavily and the effective sample is smaller than the row count suggests — widen it before reading
  a small lift as real.
- **Re-cutting needs a new directory, or a wipe.** A `sample_id` is unique only within a
  `dataset_name`, so new rows under this name are served the old measurements silently. Copy to
  `screen-taste-vN`, or delete what this name measured — the second is right while the campaign is
  scratch and wrong once a result is worth keeping (`datasets/CLAUDE.md` § Re-cutting a dataset
  needs a NEW name).

## Scoring

`list_rr(predicted, ground_truth)` — reciprocal rank of the held-out title in the returned order.
Graded, not hit/miss: ranking it first and ranking it tenth are different answers, which is the
whole reason the prompt has anything to optimize here. The answer-format contract is the live
string in `matchers.py::EXTRACTION_NOTES` — read it there, not from a paraphrase.

**Read every score against 0.2929, never against zero.** The floor is chance, not silence — and
it is `N_SLATE`'s, not a constant: `build_rows.py` derives and prints it, so a wider slate moves it
and this line is the copy to re-check.

## Follow-ups (not in v0)

- The liked pool grows past 30; row count past 20.
- Distractors drawn per-row from a much larger bank, so no candidate recurs often enough to be
  learned as "usually wrong".
- Ask the person to score the returned order directly rather than inferring from one held-out
  title — needs a judge or a rating scorer, not `list_rr`.

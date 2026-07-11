import type { DatasetItem } from "@/lib/api/types.generated";

// The ONE hard-sample ranking: Info gain (pick_score) descending, contested samples
// first, unmeasured rows last in sample_id order.
//
// The heatmap and the table each used to sort by this key themselves, and they sank
// unmeasured rows on DIFFERENT predicates — the table on dot-presence, the heatmap on
// `pick_score === null`. Those are not the same set: `pick_score` is non-null on
// prior-only rows too, so under campaign scope the heatmap and the table showed
// different orderings while the heatmap's comment asserted they agreed. One comparator,
// so they cannot disagree again.
//
// `measuredIn` is the measured-in-this-scope signal (a sample has ≥1 dot in the scope's
// per-sample series) — the honest predicate, since a prior-only row carries a pick_score
// it never earned in this scope.
export function compareHardSamples(
  a: DatasetItem,
  b: DatasetItem,
  measuredIn: (it: DatasetItem) => boolean,
): number {
  const ma = measuredIn(a);
  const mb = measuredIn(b);
  if (ma !== mb) return ma ? -1 : 1;
  if (!ma) return a.sample_id - b.sample_id;
  const pa = a.pick_score ?? Number.NEGATIVE_INFINITY;
  const pb = b.pick_score ?? Number.NEGATIVE_INFINITY;
  if (pa !== pb) return pb - pa;
  return a.sample_id - b.sample_id;
}

// Single source of truth for the headline run KPIs — best, origin, and the
// delta between them. Before this, TopStrip and ChatPane each re-ran
// `typeof dash?.best === "number" ? …` and `best - origin` with subtly
// different finite-guards, so the same campaign could show a different
// headline in the chat job-bar than elsewhere. One derivation, they cannot
// disagree.
//
// Origin is round 0 in `dash.rounds[]` (a one-candidate round labelled "C0");
// its accuracy is the round-0 entry's accuracy. The candidate list
// (round-candidates.ts) reads the same round-0 entry through the generic loop.

import type { RoundSummary } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { fmtPct0 } from "@/lib/format";

// Which fitness number headlines a per-candidate text surface. DISPLAY only —
// the engine always GATES on difficulty-adjusted ability θ; this just picks what
// the operator reads (θ is jargon, so it is never the forced default). Seeded
// from the served `dash.headline_metric` (CampaignConfig.headline_metric),
// client-overridable. `composite_fitness` is served on every settled row
// (dashboard candidates AND `/tree` nodes), so all cycles honor the composite
// selection on one basis.
export type HeadlineMetric = "accuracy" | "composite" | "ability";

// The toggle's options, in display order, each with the teaching tooltip that
// keeps θ from reading as an unexplained jargon number (the `AbilityInfo`
// popover carries the long form).
// Display order — accuracy first (always shown; a candidate is rarely bad on it), then ability θ
// (usually the metric the winner is elected on), then composite. `primaryMetric` reads this order
// as its TIE-BREAK only: the elected metric wins when it is on.
//
// `glyph` is the notation each number is already written in, which is why it IS the icon rather
// than a picture of one. It lives here because this table is the one owner of a metric's name,
// prose and order — `candidates/series.ts` joins to it rather than restating any of the three.
export const HEADLINE_METRICS: { id: HeadlineMetric; glyph: string; title: string }[] = [
  {
    id: "accuracy",
    glyph: "%",
    title: "Raw accuracy — correctness rate over the candidate's measured subset (subset-relative).",
  },
  {
    id: "ability",
    glyph: "θ",
    title:
      "Difficulty-adjusted ability θ — the metric the winner is actually elected on. A logit (not a %): comparable within a round; cross-round comparison waits on the stable δ bank.",
  },
  {
    id: "composite",
    glyph: "∑",
    title:
      "Composite fitness under the active scoring formula (equals accuracy when no formula is set).",
  },
];

export function headlineMetricLabel(m: HeadlineMetric): string {
  return m === "ability" ? "ability θ" : m === "composite" ? "composite" : "accuracy";
}

// The candidates card's metric axis is a SET (the bars can pair several series), but a node
// LABEL is one number. Prefer the metric the campaign ELECTS on where it is shown — the bars
// paint that one at full accent, and a node printing a different number under a bar that
// loud is the card disagreeing with itself. Accuracy is always seeded on and sorts first in
// canonical order, so without `elected` this silently answered "accuracy" forever, whatever
// the engine was deciding on. Canonical order is the fallback, and `fmtHeadlineValue` takes
// the result unchanged, so the tree renderers need no new formatter.
export function primaryMetric(
  metrics: ReadonlySet<HeadlineMetric>,
  elected?: HeadlineMetric,
): HeadlineMetric {
  if (elected && metrics.has(elected)) return elected;
  return HEADLINE_METRICS.find((m) => metrics.has(m.id))?.id ?? "accuracy";
}

// Format one candidate's headline number for the selected metric: a percent for
// accuracy/composite, a 2-dp logit for θ (`θ 0.41`). `null` → "—" either way.
export function fmtHeadlineValue(
  metric: HeadlineMetric,
  pct: number | null,
  theta: number | null,
): string {
  if (metric === "ability") {
    return typeof theta === "number" && Number.isFinite(theta) ? `θ ${theta.toFixed(2)}` : "—";
  }
  return fmtPct0(pct);
}

export interface HeadlineStats {
  // Served rolling-max of what each round MEASURED, finite or null.
  best: number | null;
  // Origin's round-0 measured accuracy (same basis as `best`), finite or null.
  origin: number | null;
  // Served lift over origin, in LOGITS on the cycle's fixed δ ruler — NOT a fraction, so
  // never format it with a percent. It is deliberately not `best − origin`: under
  // `per_round_resubset` each round draws a fresh subset, so that difference is the luckiest
  // draw minus the fullest one and read `+19%` off a cycle whose ability never moved. Ability
  // is the only cross-round-comparable series (see `RoundSummary.ability`).
  abilityDelta: number | null;
}

function finite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function headlineStats(dash: DashboardSnapshot | null): HeadlineStats {
  // `best` is the server-side rolling max of `rounds[].accuracy` — what each round
  // actually MEASURED (LiveDashboardView._absorb_round_complete is the sole writer;
  // it is NOT composite-based). `abilityDelta` is SERVED (`ability_delta`) — never
  // recomputed here, so this chip and the L4 inner progress line read one number (R-36).
  // The two are on DIFFERENT bases now, deliberately: `best` answers "what did a round
  // measure", `abilityDelta` answers "how far above origin is the incumbent".
  const best = finite(dash?.best);
  const round0 = (dash?.rounds ?? []).find((r) => r.round === 0);
  const origin = round0 ? finite(round0.accuracy) : null;
  const abilityDelta = finite(dash?.ability_delta);
  return { best, origin, abilityDelta };
}

export interface FitnessTrend {
  // Per-round measured accuracy, ascending. `theta` is the subset-invariant peer beside it,
  // `null` on any round read while the ruler was still cold.
  points: { round: number; composite: number; theta: number | null; n: number | null }[];
  // Running-best composite, index-aligned with `points`.
  best: number[];
}

// One definition of the campaign trend behind the TopStrip sparkline and the
// TrendChart best-line, so they can't drift on the source rule or the
// running-best fold. Takes `rounds` so callers memo on `dash?.rounds`.
//
// Plots each round's MEASURED `accuracy` — the elected winner on the samples that
// round drew, or a held round's retained incumbent re-scored on them. It matches the
// Best/current tiles, which settle to the same basis.
//
// Never `cumulative_accuracy`: it pools rows measured by DIFFERENT configurations, so the line
// can sit above everything the cycle has actually measured. Under `per_round_resubset` each
// round draws a fresh hard-first subset, so one prompt swings and can read as a false "great
// start → decay" — answered by the round's own sample count beside the number, and by `theta`,
// which is subset-invariant, rides each point, and gets its own axis on `TrendChart` because a
// logit is unbounded and signed and cannot share the `0..1` one accuracy is drawn on.
export function fitnessTrend(
  rounds: readonly RoundSummary[] | undefined,
  servedBest?: number | null,
): FitnessTrend {
  const sorted = [...(rounds ?? [])].sort((a, b) => a.round - b.round);
  // The series' ruler is the first one stamped. A round read on a different δ scale is a
  // different quantity, so its θ is DROPPED rather than plotted — a mixed line is a chart of
  // two rulers wearing one axis, and nothing on it says which point came from where.
  const seriesRuler = sorted.find((r) => r.ability?.ruler_id != null)?.ability?.ruler_id ?? null;
  const points = sorted.map((r) => ({
    round: r.round,
    composite: r.accuracy,
    theta:
      r.ability != null && r.ability.ruler_id != null && r.ability.ruler_id === seriesRuler
        ? r.ability.theta
        : null,
    // Read off the winner's own row: the round-level count is not on this summary, and the
    // arms of one round all measured the same draw.
    n: r.candidates.find((c) => c.is_winner)?.scored_samples ?? null,
  }));
  const best: number[] = [];
  let runningBest = 0;
  for (const p of points) {
    runningBest = Math.max(runningBest, p.composite);
    best.push(runningBest);
  }
  // Anchor the terminal point to the SERVED `dash.best` (the engine's own fold,
  // `_absorb_round_complete`) so the chart's running-best line can never end
  // below the Best tile — e.g. a fork whose seed carried a best its own
  // rounds[] doesn't reach.
  const last = best.length - 1;
  if (last >= 0 && servedBest != null && Number.isFinite(servedBest)) {
    best[last] = Math.max(best[last] ?? 0, servedBest);
  }
  return { points, best };
}

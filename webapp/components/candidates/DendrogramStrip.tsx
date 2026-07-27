"use client";
import { memo, useMemo } from "react";
import { cx } from "@/lib/cx";
import { fmtHeadlineValue, type HeadlineMetric } from "@/lib/derivations";
import { useStableContent } from "@/lib/stable";
import type { LineageNode } from "@/lib/api";
import type { CandidateView } from "@/lib/types";
import { dendrogram, type DendroRow } from "./dendrogram";
import type { PlotGeometry } from "./FitnessChart";

// The genealogy hanging under the fitness bars, sharing their x-axis.
//
// This is the merge: the bars and this strip plot the SAME flat candidate spine
// (C0, C1.1, C1.2, C2.1 …), so laying the tree out in that sequence — instead of
// stacking each round's candidates vertically, as the forest does — makes the
// tree's x map 1:1 onto the bar categories. Every candidate's ancestry is then
// legible directly beneath its own bar.
//
// x is a PERCENTAGE of this SVG's viewport, and the viewport is inset by the
// chart's own plot-area gutters — so the nodes land on the bar centres and KEEP
// landing on them across any resize, with no JS. See `PlotGeometry`.

interface Props {
  views: CandidateView[];
  plot: PlotGeometry | null;
  // Which number to paint under a node — the card's primary metric.
  metric: HeadlineMetric;
  selectedKey: string | null;
  onSelect: (view: CandidateView | null) => void;
  // Candidates a sibling cycle was forked from → the ⑂ mark. Clicking it frees
  // the hierarchy: the card swaps to the Forest view with that cycle opened.
  forkedFrom: ReadonlyMap<string, LineageNode>;
  // Which view keys are fork bars — sibling courses trailing the candidate
  // spine. They keep their bar slot but join no round band, and clicking one
  // opens that course rather than selecting a candidate.
  forkKeys: ReadonlySet<string>;
  onFreeHierarchy: (course: LineageNode) => void;
}

export const DendrogramStrip = memo(function DendrogramStrip({
  views,
  plot,
  metric,
  selectedKey,
  onSelect,
  forkedFrom,
  forkKeys,
  onFreeHierarchy,
}: Props) {
  // Structure only — the metric NUMBER painted on each node is looked up OUTSIDE
  // this memo, so a per-sample value tick costs a text re-render and never a
  // repack of the geometry.
  const rows = useStableContent(
    useMemo<DendroRow[]>(
      () =>
        views.map((v) => ({
          key: v.key,
          round: v.round,
          label: v.label,
          candidate_id: v.candidate_id,
          is_winner: v.is_winner,
          is_fork: forkKeys.has(v.key),
        })),
      [views, forkKeys],
    ),
  );
  const geo = useMemo(() => dendrogram(rows, plot?.centers ?? []), [rows, plot]);
  const byKey = useMemo(() => new Map(views.map((v) => [v.key, v])), [views]);

  if (!plot) return null;

  const pct = (f: number) => `${f * 100}%`;

  return (
    <div
      className="cand-dendro"
      // The plot-area gutters as padding, so this SVG's viewport IS the chart's
      // plot area and the percentage x's below resolve to the bar centres.
      style={{ paddingLeft: plot.left, paddingRight: plot.rightGutter, height: geo.height }}
    >
      <svg
        width="100%"
        height={geo.height}
        className="cand-dendro-svg"
        role="group"
        aria-label="Candidate genealogy — each candidate descends from the last winning round's winner"
      >
        {geo.brackets.map((b) => (
          <line
            key={`b${b.round}`}
            className="cand-dendro-beam"
            x1={pct(b.x1f)}
            y1={b.y}
            x2={pct(b.x2f)}
            y2={b.y}
          />
        ))}
        {geo.stubs.map((s, i) => (
          <line
            key={`s${i}`}
            className={cx("cand-dendro-stub", s.kind)}
            x1={pct(s.xf)}
            y1={s.y1}
            x2={pct(s.xf)}
            y2={s.y2}
          />
        ))}
        {geo.nodes.map((n) => {
          const view = byKey.get(n.key);
          const selected = n.key === selectedKey;
          const forkCycle = forkedFrom.get(n.candidateId);
          const value = view
            ? fmtHeadlineValue(metric, metric === "composite" ? view.composite : view.accuracy, view.theta)
            : "—";
          return (
            <g key={n.key} className="cand-dendro-node">
              <g
                role="button"
                tabIndex={0}
                aria-pressed={selected}
                aria-label={
                  n.isFork
                    ? `Fork ${n.label} — a sibling course cut from this cycle; opens it — ${value}`
                    : `Candidate ${n.label}${n.isWinner ? ", round winner" : ""} — ${value}`
                }
                className={cx("cand-dendro-hit", selected && "selected")}
                onClick={() => onSelect(selected ? null : (view ?? null))}
                onKeyDown={(e) => {
                  if (e.key !== "Enter" && e.key !== " ") return;
                  e.preventDefault();
                  onSelect(selected ? null : (view ?? null));
                }}
              >
                <title>
                  {n.label}
                  {n.isFork
                    ? " · a fork — a sibling course cut from this cycle. Click to open it."
                    : n.isWinner
                      ? " · round winner (the incumbent this round elected)"
                      : " · eliminated"}
                </title>
                {/* Transparent backing rect — the 3px dot alone is an unfair
                    click/focus target. Mirrors the workflow node's hit rect. */}
                <rect x={pct(n.xf)} y={0} width={1} height={geo.height} className="cand-dendro-hitrect" />
                <circle
                  className={cx("cand-dendro-dot", n.isFork ? "fork" : n.isWinner ? "winner" : "eliminated")}
                  cx={pct(n.xf)}
                  cy={n.y}
                  r={3}
                />
              </g>
              {forkCycle && (
                <g
                  role="button"
                  tabIndex={0}
                  aria-label={`A sibling cycle was forked from ${n.label} — open the forest view on it`}
                  className="cand-dendro-fork"
                  onClick={() => onFreeHierarchy(forkCycle)}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter" && e.key !== " ") return;
                    e.preventDefault();
                    onFreeHierarchy(forkCycle);
                  }}
                >
                  <title>Forked here — open the forest view on the sibling cycle</title>
                  <text className="cand-dendro-fork-glyph" x={pct(n.xf)} y={n.y - 6}>
                    ⑂
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
});

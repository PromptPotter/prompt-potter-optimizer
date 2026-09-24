"use client";
import { memo, useMemo } from "react";
import { cx } from "@/lib/cx";
import { fmtHeadlineValue, type HeadlineMetric } from "@/lib/derivations";
import { useStableContent } from "@/lib/stable";
import { pressable } from "@/components/ui";
import type { LineageNode } from "@/lib/api";
import type { CandidateView } from "@/lib/types";
import { dendrogram, type DendroRow } from "./dendrogram";
import type { PlotGeometry } from "./FitnessChart";

// The genealogy under the fitness bars, plotting the same flat candidate spine so x maps 1:1 onto
// bar categories. x is a percentage of a viewport inset by the chart's gutters (`PlotGeometry`).

interface Props {
  views: CandidateView[];
  plot: PlotGeometry | null;
  metric: HeadlineMetric;
  selectedKey: string | null;
  onSelect: (view: CandidateView | null) => void;
  forkedFrom: ReadonlyMap<string, LineageNode>;
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
            className="cand-dendro-stub"
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
                {...pressable(() => onSelect(selected ? null : (view ?? null)))}
                aria-pressed={selected}
                aria-label={
                  n.isFork
                    ? `Fork ${n.label} — a sibling course cut from this cycle; opens it — ${value}`
                    : `Candidate ${n.label}${n.isElected ? ", round winner" : ""} — ${value}`
                }
                className={cx("cand-dendro-hit", selected && "selected")}
              >
                <title>
                  {n.label}
                  {n.isFork
                    ? " · a fork — a sibling course cut from this cycle. Click to open it."
                    : n.isElected
                      ? " · round winner (the parent this round elected)"
                      : n.isWinner
                        ? " · the round's only arm — it advances without an election"
                        : " · eliminated"}
                </title>
                {/* Centred on the dot: a full-height stub would take the hit test off every node it crosses. */}
                <circle cx={pct(n.xf)} cy={n.y} r={8} className="cand-dendro-hitarea" />
                <circle
                  className={cx("cand-dendro-dot", n.isFork ? "fork" : n.isWinner ? "winner" : "eliminated")}
                  cx={pct(n.xf)}
                  cy={n.y}
                  r={3}
                />
              </g>
              {forkCycle && (
                <g
                  {...pressable(() => onFreeHierarchy(forkCycle))}
                  aria-label={`A sibling cycle was forked from ${n.label} — open the forest view on it`}
                  className="cand-dendro-fork"
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

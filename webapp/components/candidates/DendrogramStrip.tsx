"use client";
import { memo, useMemo } from "react";
import { cx } from "@/lib/cx";
import { barValueLabel, type DisplayMetric } from "@/lib/derivations";
import { useStableContent } from "@/lib/stable";
import { pressable } from "@/components/ui";
import type { CourseNode } from "@/lib/api";
import type { CandidateBar } from "@/lib/types";
import { dendroRow, dendrogram, type DendroRow } from "./dendrogram";
import type { PlotGeometry } from "./FitnessChart";

interface Props {
  views: CandidateBar[];
  plot: PlotGeometry | null;
  metric: DisplayMetric;
  selectedKey: string | null;
  onSelect: (view: CandidateBar | null) => void;
  forkedFrom: ReadonlyMap<string, CourseNode>;
  onFreeHierarchy: (course: CourseNode) => void;
}

export const DendrogramStrip = memo(function DendrogramStrip({
  views,
  plot,
  metric,
  selectedKey,
  onSelect,
  forkedFrom,
  onFreeHierarchy,
}: Props) {
  const rows = useStableContent(useMemo<DendroRow[]>(() => views.map(dendroRow), [views]));
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
        {/* Index in the key: a repair re-measures without re-minting, so two bars can share an address. */}
        {geo.nodes.map((n, i) => {
          const view = byKey.get(n.key);
          const selected = n.key === selectedKey;
          const forkCycle = forkedFrom.get(n.candidateId);
          const value = view ? barValueLabel(metric, view) : "—";
          return (
            <g key={`${n.key}|${i}`} className="cand-dendro-node">
              <g
                {...pressable(() => onSelect(selected ? null : (view ?? null)))}
                aria-pressed={selected}
                aria-label={
                  n.isFork
                    ? `Fork ${n.label} — a sibling course cut from this cycle; opens it — ${value}`
                    : `Candidate ${n.label}${n.crown === "elected" ? ", round winner" : ""} — ${value}`
                }
                className={cx("cand-dendro-hit", selected && "selected")}
              >
                <title>
                  {n.label}
                  {n.isFork
                    ? " · a fork — a sibling course cut from this cycle. Click to open it."
                    : n.crown === "elected"
                      ? " · round winner (the parent this round elected)"
                      : n.crown === "uncontested"
                        ? " · the round's only arm — it advances without an election"
                        : n.isWinner
                          ? " · selected — its round has not closed yet"
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

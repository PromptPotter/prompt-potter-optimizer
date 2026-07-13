"use client";
import { TERMS } from "@/lib/terms";
import { type Row } from "./meta";
import { whatifIconFor } from "./icons";
import { DEFAULT_WHATIF_WEIGHT } from "./fitness-bars";
import type { CandidateView } from "@/lib/types";
import { FitnessRankSummary } from "./FitnessRankSummary";

interface Props {
  rows: Row[];
  selected: Set<string>;
  inActive: Set<string>;
  weights: Readonly<Record<string, number>>;
  views: CandidateView[];
  onToggle: (name: string) => void;
  onWeight: (name: string, weight: number) => void;
}

// The what-if ablation widget: pick evaluators + set each one's weight to
// recompute candidate scores as a weighted sum (the bar twin of the served
// `?mask=` formula) and watch the ranking shift. The on-disk composite is
// untouched. Weights seed from the realized composite coefficients.
export function WhatIfGrid({
  rows,
  selected,
  inActive,
  weights,
  views,
  onToggle,
  onWeight,
}: Props) {
  return (
    <div className="fitness-whatif">
      <div className="whatif-legend">
        <span><span className="swatch checked">✓</span>selected (counts in what-if)</span>
        <span><span className="swatch active" />used in actual formula</span>
        <span><span className="swatch optional" />available, not in formula</span>
      </div>
      <div className="whatif-grid-wrap">
        <div className="whatif-grid">
          {rows.map((r, idx) => {
            const enabled = selected.has(r.displayName);
            const inAct = inActive.has(r.displayName);
            const cls = ["whatif-sq"];
            if (enabled) cls.push("on");
            if (inAct) cls.push("in-active");
            if (!r.applicable) cls.push("disabled");
            const dirClass = r.direction === "low" ? "down" : "up";
            const dirGlyph = r.direction === "low" ? "↓" : "↑";
            const dirTip = r.direction === "low" ? TERMS.whatif_down : TERMS.whatif_up;
            const weight = weights[r.displayName] ?? DEFAULT_WHATIF_WEIGHT;
            return (
              <div key={`${r.registryName}__${r.displayName}__${idx}`} className={cls.join(" ")}>
                <button
                  type="button"
                  className="whatif-sq-toggle"
                  disabled={!r.applicable}
                  role="checkbox"
                  aria-checked={enabled}
                  aria-disabled={!r.applicable}
                  aria-label={r.displayName}
                  tabIndex={r.applicable ? 0 : -1}
                  title={r.description || r.displayName}
                  onClick={() => r.applicable && onToggle(r.displayName)}
                >
                  <span className="whatif-tick" aria-hidden="true">
                    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M2.5 8.5 L6.5 12.5 L13.5 3.5" />
                    </svg>
                  </span>
                  <span className={`whatif-dir ${dirClass}`} title={dirTip} aria-hidden="true">{dirGlyph}</span>
                  <span className="whatif-ico">
                    {whatifIconFor(r.displayName, r.registryName)}
                  </span>
                  <span className="whatif-name">{r.displayName}</span>
                </button>
                {/* Weight thermometer — only when this evaluator counts in the
                    what-if. Drives the weighted recompute (bars + lineage lens).
                    Seeded from the realized composite coefficient. */}
                <div className="whatif-weight" aria-hidden={!enabled || undefined}>
                  {enabled && r.applicable && (
                    <>
                      <input
                        type="range"
                        className="whatif-weight-range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={weight}
                        aria-label={`${r.displayName} weight`}
                        title={`Weight of ${r.displayName} in the what-if score`}
                        onChange={(e) => onWeight(r.displayName, parseFloat(e.target.value))}
                      />
                      <span className="whatif-weight-val">{weight.toFixed(2)}</span>
                    </>
                  )}
                </div>
              </div>
            );
          })}
          {rows.length === 0 && (
            <div className="fitness-empty" style={{ gridColumn: "1 / -1" }}>
              Evaluator registry loads once the optimizer publishes round 1.
            </div>
          )}
        </div>
      </div>
      <div className="whatif-summary">
        <FitnessRankSummary views={views} selected={selected} />
      </div>
    </div>
  );
}

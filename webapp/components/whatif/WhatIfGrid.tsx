"use client";
import { TERMS } from "@/lib/terms";
import { type Row } from "./meta";
import { whatifIconFor } from "./icons";
import { type BarSlot } from "./FitnessChart";
import { FitnessRankSummary } from "./FitnessRankSummary";

interface Props {
  rows: Row[];
  selected: Set<string>;
  inActive: Set<string>;
  bars: BarSlot[];
  onToggle: (name: string) => void;
}

// The what-if ablation widget: pick evaluators to recompute candidate scores
// client-side as `mean(direction-corrected selected)` and watch the ranking
// shift. The actual `composite_fitness` on disk is untouched.
export function WhatIfGrid({
  rows,
  selected,
  inActive,
  bars,
  onToggle,
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
            return (
              <button
                key={`${r.registryName}__${r.displayName}__${idx}`}
                type="button"
                className={cls.join(" ")}
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
        <FitnessRankSummary bars={bars} selected={selected} />
      </div>
    </div>
  );
}

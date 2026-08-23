"use client";
// The "fixed sample set" utility for the per-candidate fitness chart, lifted out
// of the candidates card so it's a self-contained, reusable unit (a future monitoring /
// export surface can mount it as-is). It reads/writes the shared
// `SelectionContext.sampleSet` axis directly — no prop-drilling — and owns only
// its local detail-drill state.
//
// Layout: the per-sample chip strip is the MAIN INFO (every campaign sample,
// highlighted = in the set the bars use); everything below it is control of that
// strip — clear/fill, per-round aggregate picks, and an opt-in trajectory drill.

import { useState, type CSSProperties } from "react";
import type { MeasuredUnit, OverlapReading, RoundSummary } from "@/lib/api/types";
import { unitCount } from "@/lib/format";
import { useSelection } from "@/lib/SelectionContext";
import {
  measuredUniverse,
  roundMeasuredSets,
  roundsCoveringSample,
  sameSampleSet,
  toggleInSet,
} from "@/lib/sample-set";
import { SampleTrajectorySeries } from "@/components/dashboard/samples/SampleTrajectory";

const BTN: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--text-xs)",
  padding: "1px 8px",
  border: "0.5px solid var(--color-border)",
  borderRadius: 2,
  cursor: "pointer",
  background: "transparent",
  color: "var(--color-text-secondary)",
};

function activeStyle(on: boolean): CSSProperties {
  return {
    ...BTN,
    borderColor: on ? "var(--color-new-border)" : "var(--color-border)",
    background: on ? "var(--color-new-bg)" : "transparent",
    color: on ? "var(--color-new)" : "var(--color-text-secondary)",
  };
}

export function SampleSetControl({
  rounds,
  overlap,
  unit,
}: {
  rounds: RoundSummary[];
  overlap: OverlapReading | null;
  unit: MeasuredUnit;
}) {
  const { sampleSet, setSelectionForSampleSet } = useSelection();
  const [detailOpen, setDetailOpen] = useState(false);
  const [includePlanned, setIncludePlanned] = useState(false);

  if (sampleSet == null) return null; // mode off — nothing to control

  const universe = measuredUniverse(rounds);
  const roundSets = roundMeasuredSets(rounds);
  const inSet = new Set(sampleSet);
  // Which cells are the fixed yardstick, and how widely each cell was measured. A chip every
  // round bought can carry a cross-round comparison; one a single round bought cannot, and
  // before this they looked the same, so a bar computed over seven cells read like a result.
  const coverage = roundsCoveringSample(rounds);
  const fullyCovered = roundSets.length;
  // The engine's own shared set — the cells EVERY member of the winner trajectory answered. A
  // stronger guarantee than the coverage count beside it: that one says how many ROUNDS bought a
  // cell, this says the adopted line has all of it, which is what makes a cross-round difference
  // legitimate. Served, so the strip and the chart's trajectory bars are the same set by
  // construction.
  const shared = new Set(overlap?.sample_ids ?? []);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "6px 8px",
        marginBottom: 6,
        border: "0.5px solid var(--color-new-border)",
        borderRadius: 3,
      }}
    >
      {/* MAIN INFO — every campaign sample; highlighted = in the set the bars
          are computed over. Click any to toggle. */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 3 }}>
        {universe.map((sid) => {
          const on = inSet.has(sid);
          const seen = coverage.get(sid) ?? 0;
          const everywhere = seen >= fullyCovered && fullyCovered > 0;
          return (
            <button
              key={sid}
              type="button"
              aria-pressed={on}
              onClick={() => setSelectionForSampleSet(toggleInSet(sampleSet, sid))}
              title={
                `Sample #${sid} — ${on ? "in" : "not in"} the fitness set. Click to toggle. ` +
                `Measured in ${seen}/${fullyCovered} rounds` +
                (everywhere ? " — every bar can be read on it." : " — a bar for a round that never bought it is blank, not zero.") +
                (shared.has(sid) ? " On the winner trajectory's shared set: C0 and every winner since answered it." : "")
              }
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "var(--text-xs)",
                minWidth: 22,
                padding: "1px 4px",
                borderRadius: 2,
                cursor: "pointer",
                border: "0.5px solid var(--color-border)",
                // Three independent facts, three channels, so none hides another: SELECTED is
                // the fill (what the bars use), COVERAGE is the opacity (whether they can be
                // read on it), and a cell on the trajectory's SHARED SET is underlined in its
                // own colour — the one basis on which C0 and every winner are all readable.
                textDecoration: shared.has(sid) ? "underline" : "none",
                textDecorationColor: "var(--color-overlap)",
                textUnderlineOffset: 2,
                borderColor: on ? "var(--color-new-border)" : "var(--color-border)",
                background: on ? "var(--color-new-bg)" : "var(--color-background-secondary)",
                color: on ? "var(--color-new)" : everywhere ? "var(--color-text-secondary)" : "var(--color-text-tertiary)",
                fontWeight: on ? 600 : 400,
                opacity: everywhere ? 1 : 0.55,
              }}
            >
              {sid}
            </button>
          );
        })}
      </div>

      {/* Controls for the strip above. */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <button type="button" onClick={() => setSelectionForSampleSet(universe)} style={BTN}>
          All measured
        </button>
        <button
          type="button"
          onClick={() => setSelectionForSampleSet([])}
          title="Deselect every sample (stays in sample-set mode — pick samples one by one). Click the Sample-set chip to leave the mode."
          style={BTN}
        >
          Off
        </button>
        {overlap != null && (
          <button
            type="button"
            aria-pressed={sameSampleSet(sampleSet, overlap.sample_ids)}
            onClick={() => setSelectionForSampleSet(overlap.sample_ids)}
            title={`The ${unitCount(overlap.sample_ids.length, unit)} every candidate on the winner trajectory has answered — the one basis C0 and each winner can be differenced on. Same set the trajectory bars use.`}
            style={activeStyle(sameSampleSet(sampleSet, overlap.sample_ids))}
          >
            trajectory · {overlap.sample_ids.length}
          </button>
        )}
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
          round:
        </span>
        {roundSets.map((rs) => (
          <button
            key={rs.round}
            type="button"
            aria-pressed={sameSampleSet(sampleSet, rs.ids)}
            onClick={() => setSelectionForSampleSet(rs.ids)}
            title={`${unitCount(rs.ids.length, unit)} measured in round ${rs.round}`}
            style={activeStyle(sameSampleSet(sampleSet, rs.ids))}
          >
            R{rs.round}
          </button>
        ))}
        <span
          style={{
            marginLeft: "auto",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-xs)",
            color: "var(--color-text-tertiary)",
          }}
        >
          {sampleSet.length}/{universe.length}
          · composite/what-if off
        </span>
      </div>

      {/* Detail drill — quiet text link; the spacious trajectory stays collapsed
          and out of the way until asked for. */}
      <button
        type="button"
        aria-expanded={detailOpen}
        onClick={() => setDetailOpen((v) => !v)}
        style={{
          alignSelf: "flex-start",
          padding: 0,
          border: "none",
          background: "transparent",
          cursor: "pointer",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-xs)",
          color: "var(--color-text-tertiary)",
          textDecoration: "underline",
        }}
      >
        {detailOpen ? "hide detail" : "pick a state in detail…"}
      </button>
      {detailOpen && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)" }}>
              a square loads:
            </span>
            <button
              type="button"
              aria-pressed={!includePlanned}
              onClick={() => setIncludePlanned(false)}
              style={activeStyle(!includePlanned)}
            >
              measured only
            </button>
            <button
              type="button"
              aria-pressed={includePlanned}
              onClick={() => setIncludePlanned(true)}
              style={activeStyle(includePlanned)}
            >
              + planned
            </button>
          </div>
          <SampleTrajectorySeries
            rounds={rounds}
            selectMode={includePlanned ? "all" : "measured"}
            maxHeight={200}
          />
        </div>
      )}
    </div>
  );
}

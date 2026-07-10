"use client";
// The "fixed sample set" utility for the per-candidate fitness chart, lifted out
// of FitnessPanel so it's a self-contained, reusable unit (a future monitoring /
// export surface can mount it as-is). It reads/writes the shared
// `SelectionContext.sampleSet` axis directly — no prop-drilling — and owns only
// its local detail-drill state.
//
// Layout: the per-sample chip strip is the MAIN INFO (every campaign sample,
// highlighted = in the set the bars use); everything below it is control of that
// strip — clear/fill, per-round aggregate picks, and an opt-in trajectory drill.

import { useState, type CSSProperties } from "react";
import type { RoundSummary } from "@/lib/api/types";
import { useSelection } from "@/lib/SelectionContext";
import {
  measuredUniverse,
  roundMeasuredSets,
  sameSampleSet,
  toggleInSet,
} from "@/lib/sample-set";
import { SampleTrajectorySeries } from "@/components/dashboard/samples/SampleTrajectory";
import { NEW_BG, NEW_BORDER, NEW_COLOR } from "@/components/dashboard/samples/trajectoryStyles";

const BTN: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
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
    borderColor: on ? NEW_BORDER : "var(--color-border)",
    background: on ? NEW_BG : "transparent",
    color: on ? NEW_COLOR : "var(--color-text-secondary)",
  };
}

export function SampleSetControl({ rounds }: { rounds: RoundSummary[] }) {
  const { sampleSet, setSelectionForSampleSet } = useSelection();
  const [detailOpen, setDetailOpen] = useState(false);
  const [includePlanned, setIncludePlanned] = useState(false);

  if (sampleSet == null) return null; // mode off — nothing to control

  const universe = measuredUniverse(rounds);
  const roundSets = roundMeasuredSets(rounds);
  const inSet = new Set(sampleSet);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "6px 8px",
        marginBottom: 6,
        border: "0.5px solid var(--color-border)",
        borderRadius: 3,
        background: "rgba(59, 130, 246, 0.08)",
      }}
    >
      {/* MAIN INFO — every campaign sample; highlighted = in the set the bars
          are computed over. Click any to toggle. */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 3 }}>
        {universe.map((sid) => {
          const on = inSet.has(sid);
          return (
            <button
              key={sid}
              type="button"
              aria-pressed={on}
              onClick={() => setSelectionForSampleSet(toggleInSet(sampleSet, sid))}
              title={`Sample #${sid} — ${on ? "in" : "not in"} the fitness set. Click to toggle.`}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                minWidth: 22,
                padding: "1px 4px",
                borderRadius: 2,
                cursor: "pointer",
                border: "0.5px solid var(--color-border)",
                borderColor: on ? "rgba(59,130,246,0.55)" : "var(--color-border)",
                background: on ? "rgba(59,130,246,0.18)" : "var(--color-background-secondary)",
                color: on ? NEW_COLOR : "var(--color-text-tertiary)",
                fontWeight: on ? 600 : 400,
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
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--color-text-tertiary)" }}>
          round:
        </span>
        {roundSets.map((rs) => (
          <button
            key={rs.round}
            type="button"
            aria-pressed={sameSampleSet(sampleSet, rs.ids)}
            onClick={() => setSelectionForSampleSet(rs.ids)}
            title={`${rs.ids.length} samples measured in round ${rs.round}`}
            style={activeStyle(sameSampleSet(sampleSet, rs.ids))}
          >
            R{rs.round}
          </button>
        ))}
        <span
          style={{
            marginLeft: "auto",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--color-text-tertiary)",
          }}
        >
          {sampleSet.length}/{universe.length} · composite/what-if off
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
          fontSize: 10,
          color: "var(--color-text-tertiary)",
          textDecoration: "underline",
        }}
      >
        {detailOpen ? "hide detail" : "pick a state in detail…"}
      </button>
      {detailOpen && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--color-text-tertiary)" }}>
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

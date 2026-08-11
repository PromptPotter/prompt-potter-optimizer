"use client";
import { useMemo, useState } from "react";
import type { RoundSummary } from "@/lib/api/types";
import { CardFrame } from "@/components/ui";
import {
  type SelectMode,
  buildSorted,
  classifyCell,
  cumulativeEverSeen,
  unionFirstAppearance,
  type SortedRounds,
} from "@/lib/derivations";
import { SeriesView } from "./TrajectorySeriesView";
import { ROW_LABEL, SQ_ADD, SQ_DROP, SQ_KEPT } from "./trajectoryStyles";

interface Props {
  rounds: RoundSummary[];
}

type ViewKind = "delta" | "series";

// Queue-mechanism objective label — hardcoded since the adaptive queue
// mechanism is a single function (decision_information_gain in
// adaptive_queue_mechanism.py). If objective variants land, source this
// from a new RoundSummary field.
const OBJECTIVE_LABEL = "by decision_information_gain";

// Mini-button trigger — fixed dimensions matching `.hs-heat-mini-btn`
// (57 × 22 px, no resize). Inner texture is a miniature Series-view grid
// (4 × 4 px tiles) so the button itself previews what's inside.
export function SampleTrajectoryMiniButton({
  expanded,
  rounds,
  onToggle,
}: {
  expanded: boolean;
  rounds: RoundSummary[];
  onToggle: () => void;
}) {
  const sorted = useMemo(() => buildSorted(rounds), [rounds]);
  const columns = useMemo(() => unionFirstAppearance(sorted.rounds), [sorted.rounds]);
  const everSeen = useMemo(() => cumulativeEverSeen(sorted.rounds), [sorted.rounds]);

  const nRounds = sorted.rounds.length;
  const summary = `Sample trajectory · ${nRounds} round${nRounds === 1 ? "" : "s"}`;

  return (
    <button
      type="button"
      className="st-mini-btn"
      aria-expanded={expanded}
      aria-label={expanded ? "Collapse sample trajectory" : `Expand sample trajectory. ${summary}.`}
      onClick={onToggle}
      title={`${summary} — click to ${expanded ? "collapse" : "expand"}`}
    >
      <span className="st-mini" aria-hidden="true">
        {sorted.rounds.map((r, i) => {
          // `positions` and `everSeen` are built one-per-round, parallel to `sorted.rounds`.
          const pos = sorted.positions[i]!;
          const prev = i > 0 ? sorted.positions[i - 1]! : null;
          const everPrev = i > 0 ? everSeen[i - 1]! : new Set<number>();
          return columns.map((sid) => {
            const kind = classifyCell(sid, pos, prev, everPrev);
            return <span key={`${r.round}-${sid}`} className={`st-mini-cell ${kind}`} />;
          });
        })}
      </span>
    </button>
  );
}

// Content panel — Delta or Series view. Pure renderer; parent owns the
// expand toggle (the mini-button above).
export function SampleTrajectory({ rounds }: Props) {
  const [view, setView] = useState<ViewKind>("delta");
  const sorted = useMemo(() => buildSorted(rounds), [rounds]);

  if (sorted.rounds.length === 0) return null;

  return (
    <CardFrame
      title={<span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)" }}>Sample trajectory</span>}
      actions={
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", fontFamily: "var(--font-mono)" }}>
            {OBJECTIVE_LABEL}
          </span>
          <ViewToggle view={view} onChange={setView} />
        </div>
      }
      style={{ marginBottom: 12, width: "100%" }}
    >
      {view === "delta" ? (
        <DeltaView sorted={sorted} />
      ) : (
        <SeriesView sorted={sorted} />
      )}
    </CardFrame>
  );
}

// Standalone Series grid, reusable outside the trajectory card (the
// per-candidate fitness "Sample set" detail embeds it). Builds the sorted
// rounds, then renders the same hover-popup + click-to-select grid. `maxHeight`
// makes it vertically scrollable (≈5 rounds by default); `selectMode` controls
// whether a click selects measured-only or the whole round.
export function SampleTrajectorySeries({
  rounds,
  selectMode = "measured",
  maxHeight = 200,
}: {
  rounds: RoundSummary[];
  selectMode?: SelectMode;
  maxHeight?: number;
}) {
  const sorted = useMemo(() => buildSorted(rounds), [rounds]);
  if (sorted.rounds.length === 0) return null;
  return <SeriesView sorted={sorted} selectMode={selectMode} maxHeight={maxHeight} />;
}

function ViewToggle({ view, onChange }: { view: ViewKind; onChange: (v: ViewKind) => void }) {
  const btn = (kind: ViewKind, label: string) => {
    const active = view === kind;
    return (
      <button
        type="button"
        onClick={() => onChange(kind)}
        style={{
          padding: "2px 10px",
          fontSize: "var(--text-xs)",
          fontFamily: "var(--font-mono)",
          border: "0.5px solid var(--color-border)",
          borderRadius: 2,
          background: active ? "var(--color-background-tertiary)" : "transparent",
          color: active ? "var(--color-text-primary)" : "var(--color-text-tertiary)",
          cursor: "pointer",
        }}
        aria-pressed={active}
      >
        {label}
      </button>
    );
  };
  return (
    <div style={{ display: "inline-flex", gap: 4 }}>
      {btn("delta", "Delta")}
      {btn("series", "Series")}
    </div>
  );
}

function DeltaView({ sorted }: { sorted: SortedRounds }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {sorted.rounds.map((r, i) => {
        const bank = r.selection;
        if (i === 0) {
          return (
            <Row key={r.round} label={`R${r.round}`}>
              {bank.map((sid) => (
                <span key={sid} style={SQ_KEPT}>{sid}</span>
              ))}
            </Row>
          );
        }
        const prev = new Set(sorted.rounds[i - 1]!.selection);
        const curr = new Set(bank);
        const drops = [...prev].filter((x) => !curr.has(x));
        const adds = bank.filter((x) => !prev.has(x));
        if (drops.length === 0 && adds.length === 0) {
          return (
            <Row key={r.round} label={`R${r.round}`}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)", color: "var(--color-text-tertiary)", lineHeight: "26px" }}>
                (no change)
              </span>
            </Row>
          );
        }
        return (
          <Row key={r.round} label={`R${r.round}`}>
            {drops.map((sid) => (
              <span key={`d-${sid}`} style={SQ_DROP} title={`dropped ${sid}`}>−{sid}</span>
            ))}
            {adds.map((sid) => (
              <span key={`a-${sid}`} style={SQ_ADD} title={`added ${sid}`}>+{sid}</span>
            ))}
          </Row>
        );
      })}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 4, flexWrap: "wrap" }}>
      <span style={ROW_LABEL}>{label}</span>
      <span style={{ display: "flex", flexWrap: "wrap", gap: 3 }}>{children}</span>
    </div>
  );
}

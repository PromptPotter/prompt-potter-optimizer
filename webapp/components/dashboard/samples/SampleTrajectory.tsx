"use client";
import { useMemo, useState, type CSSProperties } from "react";
import type { RoundSummary } from "@/lib/api/types";
import { CardFrame, SegmentedControl } from "@/components/ui";
import {
  type SelectMode,
  buildSorted,
  classifyCell,
  cumulativeEverSeen,
  unionFirstAppearance,
  type SortedRounds,
} from "@/lib/derivations";
import { SeriesView } from "./TrajectorySeriesView";

interface Props {
  rounds: RoundSummary[];
}

type ViewKind = "delta" | "series";

// Queue-mechanism objective label — hardcoded since the adaptive queue
// mechanism is a single function (decision_information_gain in
// adaptive_queue_mechanism.py). If objective variants land, source this
// from a new RoundSummary field.
const OBJECTIVE_LABEL = "by decision_information_gain";

// Interior box a mini button's tile grid must fit — 57x22px button minus its
// 3px padding on every side (`.hs-mini-btn`).
const MINI_BOX_W = 51;
const MINI_BOX_H = 16;
const MINI_GAP = 1;
const MINI_MAX_TILE = 4;

// Largest tile size (in px, capped at MINI_MAX_TILE) that fits `n` tiles inside
// the mini box without any spilling past the fixed 57x22 button — the strip used
// to hold every tile at a flat 4px regardless of count, so a round x sample
// matrix wider than the box lost tiles with no scrollbar and no ellipsis to say
// so (`webapp/CLAUDE.md` § Stylesheet organization). Scaling the tile GEOMETRY
// down, never the CSS `transform`/`viewBox`, is what keeps every tile visible.
function miniTileSize(n: number): number {
  if (n <= 0) return MINI_MAX_TILE;
  for (let s = MINI_MAX_TILE; s > 1; s--) {
    const cols = Math.floor((MINI_BOX_W + MINI_GAP) / (s + MINI_GAP));
    const rows = Math.floor((MINI_BOX_H + MINI_GAP) / (s + MINI_GAP));
    if (cols * rows >= n) return s;
  }
  return 1;
}

// Mini-button trigger — fixed dimensions matching `.hs-mini-btn` (57 × 22 px,
// no resize). Inner texture is a miniature Series-view grid (tiles scaled to
// fit) so the button itself previews what's inside.
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
  const tile = miniTileSize(nRounds * columns.length);

  return (
    <button
      type="button"
      className="hs-mini-btn"
      aria-expanded={expanded}
      aria-label={expanded ? "Collapse sample trajectory" : `Expand sample trajectory. ${summary}.`}
      onClick={onToggle}
      title={`${summary} — click to ${expanded ? "collapse" : "expand"}`}
    >
      <span
        className="hs-mini-tiles"
        aria-hidden="true"
        style={{ "--hs-mini-tile": `${tile}px` } as CSSProperties}
      >
        {sorted.rounds.map((r, i) => {
          // `positions` and `everSeen` are built one-per-round, parallel to `sorted.rounds`.
          const pos = sorted.positions[i]!;
          const prev = i > 0 ? sorted.positions[i - 1]! : null;
          const everPrev = i > 0 ? everSeen[i - 1]! : new Set<number>();
          return columns.map((sid) => {
            const kind = classifyCell(sid, pos, prev, everPrev);
            return <span key={`${r.round}-${sid}`} className={`hs-mini-cell ${kind}`} />;
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
      title={<span className="st-card-title">Sample trajectory</span>}
      actions={
        <div className="st-card-actions">
          <span className="st-objective">{OBJECTIVE_LABEL}</span>
          <SegmentedControl
            ariaLabel="Sample trajectory view"
            value={view}
            onChange={setView}
            options={[
              { value: "delta", label: "Delta" },
              { value: "series", label: "Series" },
            ]}
          />
        </div>
      }
      className="st-card"
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

function DeltaView({ sorted }: { sorted: SortedRounds }) {
  return (
    <div className="st-delta">
      {sorted.rounds.map((r, i) => {
        const bank = r.selection;
        if (i === 0) {
          return (
            <Row key={r.round} label={`R${r.round}`}>
              {bank.map((sid) => (
                <span key={sid} className="st-sq kept">{sid}</span>
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
              <span className="st-nochange">(no change)</span>
            </Row>
          );
        }
        return (
          <Row key={r.round} label={`R${r.round}`}>
            {drops.map((sid) => (
              <span key={`d-${sid}`} className="st-sq drop" title={`dropped ${sid}`}>−{sid}</span>
            ))}
            {adds.map((sid) => (
              <span key={`a-${sid}`} className="st-sq add" title={`added ${sid}`}>+{sid}</span>
            ))}
          </Row>
        );
      })}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="st-delta-row">
      <span className="st-row-label">{label}</span>
      <span className="st-delta-cells">{children}</span>
    </div>
  );
}

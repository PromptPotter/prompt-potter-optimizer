"use client";
import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import type { RoundSummary } from "@/lib/api/types";
import { CardFrame } from "@/components/ui/Card";

interface Props {
  rounds: RoundSummary[];
}

type ViewKind = "delta" | "series";

// Fixed accent for the "newly added" / "first appearance" semantic in the
// Series view. Distinct from success/danger so add ≠ gained-position.
const NEW_COLOR = "#3b82f6";
const NEW_BG = "rgba(59, 130, 246, 0.14)";
const NEW_BORDER = "rgba(59, 130, 246, 0.42)";

const SQ_BASE: CSSProperties = {
  width: 26,
  height: 26,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  border: "0.5px solid var(--color-border-tertiary)",
  borderRadius: 2,
  background: "var(--color-background-secondary)",
  color: "var(--color-text-secondary)",
  flexShrink: 0,
};

const SQ_DROP: CSSProperties = {
  ...SQ_BASE,
  color: "var(--color-danger)",
  borderColor: "var(--color-danger-border)",
  background: "var(--color-danger-bg)",
};

const SQ_ADD: CSSProperties = {
  ...SQ_BASE,
  color: "var(--color-success)",
  borderColor: "var(--color-success-border)",
  background: "var(--color-success-bg)",
};

const SQ_NEW: CSSProperties = {
  ...SQ_BASE,
  color: NEW_COLOR,
  borderColor: NEW_BORDER,
  background: NEW_BG,
};

const SQ_GAINED = SQ_ADD;
const SQ_LOST: CSSProperties = {
  ...SQ_BASE,
  color: "var(--color-warn)",
  borderColor: "var(--color-warn-border)",
  background: "var(--color-warn-bg)",
};

const SQ_KEPT = SQ_BASE;
const SQ_ABSENT: CSSProperties = {
  ...SQ_BASE,
  background: "var(--color-background-tertiary, #1a1a1a)",
  borderColor: "var(--color-border-secondary)",
  color: "transparent",
};

const ROW_LABEL: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  color: "var(--color-text-tertiary)",
  width: 38,
  flexShrink: 0,
  textAlign: "right",
  paddingRight: 6,
  lineHeight: "26px",
};

const COL_HEADER: CSSProperties = {
  ...SQ_BASE,
  border: "none",
  background: "transparent",
  color: "var(--color-text-tertiary)",
  fontSize: 9,
};

// Sample-id → 1-indexed position in the round's measurement order.
function positionMap(bank: number[] | undefined): Map<number, number> {
  const m = new Map<number, number>();
  if (!bank) return m;
  bank.forEach((sid, i) => m.set(sid, i + 1));
  return m;
}

// Column ordering for the Series view: union of every round's selection,
// ordered by first-appearance round, then within-round measurement
// position. Late-arriving samples sit on the right.
function unionFirstAppearance(rounds: RoundSummary[]): number[] {
  const out: number[] = [];
  const seen = new Set<number>();
  for (const r of rounds) {
    for (const sid of r.selection ?? []) {
      if (seen.has(sid)) continue;
      seen.add(sid);
      out.push(sid);
    }
  }
  return out;
}

interface SortedRounds {
  rounds: RoundSummary[];
  positions: Map<number, number>[];
}

function buildSorted(rounds: RoundSummary[]): SortedRounds {
  const sorted = [...rounds]
    .filter((r) => Array.isArray(r.selection) && r.selection.length > 0)
    .sort((a, b) => a.round - b.round);
  return {
    rounds: sorted,
    positions: sorted.map((r) => positionMap(r.selection)),
  };
}

type CellKind = "new" | "gained" | "lost" | "kept" | "absent";

function classifyCell(
  sid: number,
  pos: Map<number, number>,
  prev: Map<number, number> | null,
  everPrev: Set<number>,
): CellKind {
  const p = pos.get(sid);
  if (p === undefined) return "absent";
  if (!everPrev.has(sid)) return "new";
  const pp = prev?.get(sid);
  if (pp === undefined) return "new"; // re-added after a drop
  if (p < pp) return "gained";
  if (p > pp) return "lost";
  return "kept";
}

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
  const everSeen = useMemo<Set<number>[]>(() => {
    const out: Set<number>[] = [];
    let seen = new Set<number>();
    for (const r of sorted.rounds) {
      seen = new Set(seen);
      (r.selection ?? []).forEach((s) => seen.add(s));
      out.push(seen);
    }
    return out;
  }, [sorted.rounds]);

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
          const pos = sorted.positions[i];
          const prev = i > 0 ? sorted.positions[i - 1] : null;
          const everPrev = i > 0 ? everSeen[i - 1] : new Set<number>();
          return columns.map((sid) => {
            const kind = classifyCell(sid, pos, prev, everPrev);
            return (
              <span
                key={`${r.round}-${sid}`}
                className={`st-mini-cell ${kind}`}
              />
            );
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
      title={<span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>Sample trajectory</span>}
      actions={
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 11, color: "var(--color-text-tertiary)", fontFamily: "var(--font-mono)" }}>
            {OBJECTIVE_LABEL}
          </span>
          <ViewToggle view={view} onChange={setView} />
        </div>
      }
      style={{ marginBottom: 12, width: "100%" }}
    >
      {view === "delta" ? <DeltaView sorted={sorted} /> : <SeriesView sorted={sorted} />}
    </CardFrame>
  );
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
          fontSize: 11,
          fontFamily: "var(--font-mono)",
          border: "0.5px solid var(--color-border-tertiary)",
          borderRadius: 2,
          background: active ? "var(--color-background-tertiary, var(--color-background-secondary))" : "transparent",
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
        const bank = r.selection ?? [];
        if (i === 0) {
          return (
            <Row key={r.round} label={`R${r.round}`}>
              {bank.map((sid) => (
                <span key={sid} style={SQ_KEPT}>{sid}</span>
              ))}
            </Row>
          );
        }
        const prev = new Set(sorted.rounds[i - 1].selection ?? []);
        const curr = new Set(bank);
        const drops = [...prev].filter((x) => !curr.has(x));
        const adds = bank.filter((x) => !prev.has(x));
        if (drops.length === 0 && adds.length === 0) {
          return (
            <Row key={r.round} label={`R${r.round}`}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--color-text-tertiary)", lineHeight: "26px" }}>
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

function SeriesView({ sorted }: { sorted: SortedRounds }) {
  const columns = useMemo(() => unionFirstAppearance(sorted.rounds), [sorted.rounds]);

  // Cumulative ever-seen set per round-index so we can distinguish
  // "newly added" (never seen before) from "re-added after a drop" — both
  // get the NEW colour today, but kept separate so we can split later.
  const everSeen: Set<number>[] = [];
  let seen = new Set<number>();
  for (const r of sorted.rounds) {
    seen = new Set(seen);
    (r.selection ?? []).forEach((s) => seen.add(s));
    everSeen.push(seen);
  }

  return (
    <div style={{ overflowX: "auto", paddingBottom: 4 }}>
      <div style={{ display: "inline-flex", flexDirection: "column", gap: 3, minWidth: "fit-content" }}>
        {/* column header — sample ids */}
        <div style={{ display: "flex", gap: 3 }}>
          <span style={ROW_LABEL}>id</span>
          <span style={{ display: "flex", gap: 3 }}>
            {columns.map((sid) => (
              <span key={sid} style={COL_HEADER}>{sid}</span>
            ))}
          </span>
        </div>
        {/* one row per round */}
        {sorted.rounds.map((r, i) => {
          const pos = sorted.positions[i];
          const prev = i > 0 ? sorted.positions[i - 1] : null;
          const everPrev = i > 0 ? everSeen[i - 1] : new Set<number>();
          return (
            <div key={r.round} style={{ display: "flex", gap: 3 }}>
              <span style={ROW_LABEL}>R{r.round}</span>
              <span style={{ display: "flex", gap: 3 }}>
                {columns.map((sid) => {
                  const p = pos.get(sid);
                  if (p === undefined) {
                    return <span key={sid} style={SQ_ABSENT}>·</span>;
                  }
                  const pp = prev?.get(sid);
                  let style: CSSProperties;
                  let titleNote = "kept position";
                  if (!everPrev.has(sid)) {
                    style = SQ_NEW;
                    titleNote = "newly added";
                  } else if (pp === undefined) {
                    style = SQ_NEW;
                    titleNote = "re-added";
                  } else if (p < pp) {
                    style = SQ_GAINED;
                    titleNote = `gained: pos ${pp} → ${p}`;
                  } else if (p > pp) {
                    style = SQ_LOST;
                    titleNote = `lost: pos ${pp} → ${p}`;
                  } else {
                    style = SQ_KEPT;
                  }
                  return (
                    <span key={sid} style={style} title={`#${sid} R${r.round} pos ${p} · ${titleNote}`}>
                      {p}
                    </span>
                  );
                })}
              </span>
            </div>
          );
        })}
        <Legend />
      </div>
    </div>
  );
}

function Legend() {
  const swatch = (style: CSSProperties, label: string) => (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span style={{ ...style, width: 12, height: 12, fontSize: 0 }} />
      <span style={{ fontSize: 10, color: "var(--color-text-tertiary)", fontFamily: "var(--font-mono)" }}>{label}</span>
    </span>
  );
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 6, paddingLeft: 42 }}>
      {swatch(SQ_NEW, "new / re-added")}
      {swatch(SQ_GAINED, "gained position")}
      {swatch(SQ_LOST, "lost position")}
      {swatch(SQ_KEPT, "kept position")}
      {swatch(SQ_ABSENT, "not in bank")}
    </div>
  );
}

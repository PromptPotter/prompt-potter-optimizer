"use client";
// Outer verdict — the blocked, paired L4 read of the viewed pp-self cycle's latest
// round: per-cell (variant − origin) diffs as dots on a shared 0-centred axis, plus the
// pooled effect as a diamond with its CI whisker, and a 3-way decision. Net-new SVG
// (no CI/forest primitive existed). Sign-coloured, but the number + label always carry
// the meaning. Reads the served verdict off dash.rounds; never recomputes.

import { memo, useMemo } from "react";
import { useDashboard } from "@/lib/hooks/useDashboard";
import type { OuterVerdict, RoundSummary } from "@/lib/api/types";
import { CardFrame, Badge, type BadgeTone } from "@/components/ui";

const DECISION_TONE: Record<string, BadgeTone> = {
  adopt: "success",
  reject: "danger",
  inconclusive: "accent",
};

const AXIS_W = 220;
const ROW_H = 18;

function fmt(n: number): string {
  return (n >= 0 ? "+" : "") + n.toFixed(3);
}

function Row({
  label,
  x,
  children,
  value,
}: {
  label: string;
  x: (v: number) => number;
  children: React.ReactNode;
  value: string;
}) {
  return (
    <div className="ov-row">
      <span className="ov-cell-label" title={label}>
        {label}
      </span>
      <svg
        className="ov-axis"
        width={AXIS_W}
        height={ROW_H}
        viewBox={`0 0 ${AXIS_W} ${ROW_H}`}
        role="img"
        aria-label={`${label}: ${value}`}
      >
        <line
          x1={x(0)}
          y1={2}
          x2={x(0)}
          y2={ROW_H - 2}
          stroke="var(--color-border)"
          strokeWidth={1}
        />
        {children}
      </svg>
      <span className="ov-cell-val">{value}</span>
    </div>
  );
}

function ForestPlot({ verdict }: { verdict: OuterVerdict }) {
  const x = useMemo(() => {
    const vals = [0, verdict.ci_lo, verdict.ci_hi, ...verdict.per_cell.map((c) => c.diff)];
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const span = hi - lo || 1;
    const pad = span * 0.1;
    const d0 = lo - pad;
    const d1 = hi + pad;
    return (v: number) => 4 + ((v - d0) / (d1 - d0)) * (AXIS_W - 8);
  }, [verdict]);

  const sign =
    verdict.ci_lo > 0 ? "var(--color-success)" : verdict.ci_hi < 0 ? "var(--color-danger)" : "var(--color-text-secondary)";

  return (
    <div className="ov-forest">
      {verdict.per_cell.map((c) => (
        <Row key={c.cell} label={c.cell} x={x} value={fmt(c.diff)}>
          <circle cx={x(c.diff)} cy={ROW_H / 2} r={3} fill="var(--color-text-secondary)" />
        </Row>
      ))}
      <Row label="pooled" x={x} value={`${fmt(verdict.effect)}`}>
        <line
          x1={x(verdict.ci_lo)}
          y1={ROW_H / 2}
          x2={x(verdict.ci_hi)}
          y2={ROW_H / 2}
          stroke={sign}
          strokeWidth={1.5}
        />
        <path
          d={`M ${x(verdict.effect)} ${ROW_H / 2 - 4} L ${x(verdict.effect) + 4} ${ROW_H / 2} L ${x(verdict.effect)} ${ROW_H / 2 + 4} L ${x(verdict.effect) - 4} ${ROW_H / 2} Z`}
          fill={sign}
        />
      </Row>
    </div>
  );
}

// The LATEST post-origin round, not the latest one that happens to carry a verdict: a round can
// lose its subject or its pairing, and scanning backwards past it re-presents an older round's
// verdict as the current one. No round carrying a verdict means the cycle is not L4 at all.
function pickRound(rounds: RoundSummary[]): RoundSummary | null {
  const scored = rounds.filter((r) => r.round > 0);
  if (!scored.some((r) => r.outer_verdict)) return null;
  return scored[scored.length - 1] ?? null;
}

export const OuterVerdictPanel = memo(function OuterVerdictPanel() {
  const { dash } = useDashboard();
  const round = useMemo(() => pickRound(dash?.rounds ?? []), [dash?.rounds]);
  const verdict = round?.outer_verdict ?? null;

  return (
    <CardFrame title="Outer verdict" headingTag="h2">
      {!round ? (
        <p className="l4-empty">
          Select a pp-self cycle with a completed round to see its blocked paired verdict
          (per-cell variant−origin effects pooled across the panel).
        </p>
      ) : !verdict ? (
        <p className="l4-empty">
          {round.electable_count === 0
            ? `Round ${round.round} left no readable arm — every candidate's measurement was disqualified (mid-run abort, or degradation scoring a partial subset), so the round has no verdict subject.`
            : `Round ${round.round} has no verdict: none of its arms shares a measured cell with the cached round-0 origin.`}
        </p>
      ) : (
        <>
          <p className="l4-lede">
            <Badge tone={DECISION_TONE[verdict.decision] ?? "default"}>{verdict.decision}</Badge>{" "}
            {/* Which arm this is about. A round that crowned nobody still reports its best
                ELIGIBLE arm, and reading that as an adopted winner would overstate the round. */}
            {verdict.variant_is_winner ? "Adopted variant" : "Best eligible arm (none adopted)"}{" "}
            lifts the optimizer <strong>{fmt(verdict.effect)}</strong> [{fmt(verdict.ci_lo)},{" "}
            {fmt(verdict.ci_hi)}] across {verdict.n_cells} cell
            {verdict.n_cells === 1 ? "" : "s"} vs the cached round-0 origin.
            {verdict.decision === "inconclusive"
              ? ` CI spans 0 — smallest effect this panel could still resolve ≈ ${verdict.mde_remaining.toFixed(3)}.`
              : ""}
          </p>
          {/* The panel is a census, so a verdict over fewer cells is a NARROWED comparison, not
              a small panel — `n_cells` alone cannot tell those apart. */}
          {verdict.cells_dropped.length > 0 ? (
            <p className="l4-lede">
              {verdict.cells_dropped.length} cell
              {verdict.cells_dropped.length === 1 ? "" : "s"} measured nothing and could not be
              paired: <strong>{verdict.cells_dropped.join(", ")}</strong>. The effect above is
              narrower than the declared panel.
            </p>
          ) : null}
          {/* Which lever the spread above actually calls for. `se` alone cannot separate cells
              that each measured themselves badly from cells that genuinely disagree, and those
              take opposite next steps — sharpen the instrument, or widen the panel/candidates.
              Silent when the round could not split it (one shared cell, or arms with no
              per-cell precision): an absent split is honest, a fabricated one picks the lever. */}
          {verdict.variance ? (
            <p className="l4-lede">
              {Math.round(verdict.variance.estimation_share * 100)}% of that cell-to-cell spread is
              measurement noise (±{verdict.variance.estimation_sd.toFixed(3)} logits per cell
              against ±{verdict.variance.observed_sd.toFixed(3)} observed).{" "}
              {verdict.variance.estimation_share > 0.5
                ? "The panel is mostly re-reading itself — sharpen the cells before buying more of them."
                : "The cells genuinely differ — that spread is signal about where this optimizer prompt works."}
            </p>
          ) : null}
          <ForestPlot verdict={verdict} />
        </>
      )}
    </CardFrame>
  );
});

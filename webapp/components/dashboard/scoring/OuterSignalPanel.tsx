"use client";
// Outer signal — is the L4 panel resolving anything yet, and is it getting sharper?
//
// Two stacked reads, both served, neither recomputed here:
//   1. the leading arm's blocked lift over the origin, one row per round on a SHARED axis, so
//      round-over-round tightening is a shape rather than three numbers to hold in your head;
//   2. the panel's precision — how sharply each cell was measured against how far apart the cells
//      landed — which is the lever the spread above calls for.
//
// The shared axis is the whole point. Per-round auto-scaling makes every interval look the same
// width, which is exactly the pattern a reader is here to see change.

import { memo, useMemo } from "react";
import { useDashboard } from "@/lib/hooks/useDashboard";
import type { RoundSummary, RoundSummaryCandidate } from "@/lib/api/types";
import { CardFrame, Badge } from "@/components/ui";

const AXIS_W = 220;
const ROW_H = 18;

function fmt(n: number): string {
  return (n >= 0 ? "+" : "") + n.toFixed(3);
}

// The arm the round's verdict is about: its winner, else its best-scoring one. Mirrors the rule
// the projection uses for `panel_precision`, so the two stacks below describe one arm per round.
function leadingArm(r: RoundSummary): RoundSummaryCandidate | null {
  if (!r.candidates.length) return null;
  return (
    r.candidates.find((c) => c.is_winner) ??
    r.candidates.reduce((a, b) => (b.composite_fitness > a.composite_fitness ? b : a))
  );
}

type Lift = { round: number; lift: number; lo: number; hi: number; label: string };

function liftsOf(rounds: RoundSummary[]): Lift[] {
  const out: Lift[] = [];
  for (const r of rounds) {
    if (r.round === 0) continue;
    const c = leadingArm(r);
    if (
      !c ||
      c.matched_origin_lift === null ||
      c.matched_origin_lift_ci_lo === null ||
      c.matched_origin_lift_ci_hi === null
    )
      continue;
    out.push({
      round: r.round,
      lift: c.matched_origin_lift,
      lo: c.matched_origin_lift_ci_lo,
      hi: c.matched_origin_lift_ci_hi,
      label: c.label,
    });
  }
  return out;
}

function LiftRow({ d, x }: { d: Lift; x: (v: number) => number }) {
  // Sign-coloured, but the number and the "spans 0" wording always carry the meaning on their own.
  const clears = d.lo > 0 || d.hi < 0;
  const stroke = d.lo > 0
    ? "var(--color-success)"
    : d.hi < 0
      ? "var(--color-danger)"
      : "var(--color-text-secondary)";
  const value = `${fmt(d.lift)} [${fmt(d.lo)}, ${fmt(d.hi)}]`;
  return (
    <div className="ov-row">
      <span className="ov-cell-label" title={`${d.label} — round ${d.round}`}>
        r{d.round}
      </span>
      <svg
        className="ov-axis"
        width={AXIS_W}
        height={ROW_H}
        viewBox={`0 0 ${AXIS_W} ${ROW_H}`}
        role="img"
        aria-label={`Round ${d.round}: ${value}${clears ? "" : ", spans zero"}`}
      >
        <line x1={x(0)} y1={2} x2={x(0)} y2={ROW_H - 2} stroke="var(--color-border)" strokeWidth={1} />
        <line
          x1={x(d.lo)}
          y1={ROW_H / 2}
          x2={x(d.hi)}
          y2={ROW_H / 2}
          stroke={stroke}
          strokeWidth={1.5}
        />
        <circle cx={x(d.lift)} cy={ROW_H / 2} r={3.5} fill={stroke} />
      </svg>
      <span className="ov-cell-val">{value}</span>
    </div>
  );
}

export const OuterSignalPanel = memo(function OuterSignalPanel() {
  const { dash } = useDashboard();
  const rounds = useMemo(() => dash?.rounds ?? [], [dash?.rounds]);
  const lifts = useMemo(() => liftsOf(rounds), [rounds]);

  // ONE scale across every round — see the header note.
  const x = useMemo(() => {
    const vals = [0, ...lifts.flatMap((d) => [d.lo, d.hi])];
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = (hi - lo || 1) * 0.1;
    const [d0, d1] = [lo - pad, hi + pad];
    return (v: number) => 4 + ((v - d0) / (d1 - d0)) * (AXIS_W - 8);
  }, [lifts]);

  const latest = lifts.length ? lifts[lifts.length - 1] : null;
  const precision = useMemo(() => {
    return rounds.filter((r) => r.round > 0 && r.panel_precision).at(-1)?.panel_precision ?? null;
  }, [rounds]);

  return (
    <CardFrame title="Outer signal" headingTag="h2">
      {!latest ? (
        <p className="l4-empty">
          {rounds.some((r) => r.round > 0)
            ? "No round has two cells both its leading arm and the origin measured, so no interval can be drawn. A one-cell panel never will — the reading is honest, not missing."
            : "Select a pp-self cycle with a completed round to see whether its panel resolves anything yet."}
        </p>
      ) : (
        <>
          <p className="l4-lede">
            <Badge tone={latest.lo > 0 ? "success" : latest.hi < 0 ? "danger" : "accent"}>
              {latest.lo > 0 ? "separated" : latest.hi < 0 ? "worse" : "inconclusive"}
            </Badge>{" "}
            Round {latest.round}&rsquo;s leading arm lifts <strong>{fmt(latest.lift)}</strong> [
            {fmt(latest.lo)}, {fmt(latest.hi)}] over the origin, on the cells both measured.
            {latest.lo <= 0 && latest.hi >= 0
              ? " The interval spans 0 — this panel cannot yet tell that arm from the origin, and the point estimate above should not be read as a win."
              : ""}
          </p>
          {/* The pattern the eye is meant to learn: does the whisker shorten, and does it clear
              the zero line. Both are visible only because the axis is shared. */}
          <div className="ov-forest">
            {lifts.map((d) => (
              <LiftRow key={d.round} d={d} x={x} />
            ))}
          </div>
          {/* Which lever that spread calls for. Two bars, deliberately not their ratio: the ratio
              was served once, clamped to 1.0, and presented an impossible 5.55 as a tidy "100%
              noise". In the measurand's own units (θ logits), never fitness. */}
          {precision ? (
            <p className="l4-lede">
              Each cell was measured to ±{precision.estimation_sd.toFixed(3)} logits; the cells
              landed ±{precision.observed_sd.toFixed(3)} apart across {precision.n_cells}.{" "}
              {precision.estimation_sd >= precision.observed_sd
                ? "The panel is re-reading its own noise — sharpen the cells before buying more of them."
                : "The cells genuinely differ — that spread is signal about where this optimizer prompt works."}
            </p>
          ) : null}
        </>
      )}
    </CardFrame>
  );
});

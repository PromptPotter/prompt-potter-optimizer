"use client";

import { memo, useMemo } from "react";
import { useCycleStream } from "@/lib/poll";
import type { ArmReading, RoundAdvance, ServedRound } from "@/lib/api/types";
import { ROUND_ADVANCE_LABELS } from "@/lib/api/types.generated";
import { CardFrame, Badge } from "@/components/ui";
import { liftOf } from "@/lib/derivations";
import type { LiftSide } from "@/lib/fitness";
import { fmtSigned } from "@/lib/format";

const AXIS_W = 220;
const ROW_H = 18;

// served: domain/results.py::leading_arm — never an argmax here, which cannot apply the election's admission rule.
function leadingArm(r: ServedRound): ArmReading | null {
  return r.candidates.find((c) => c.reading.election.leading)?.reading ?? null;
}

type Lift = {
  round: number;
  lift: number;
  lo: number;
  hi: number;
  // served: LiftEstimate.side — this arm's interval against 0, never the round's verdict.
  side: LiftSide;
  label: string;
  advance: RoundAdvance;
};

function liftsOf(rounds: ServedRound[]): Lift[] {
  const out: Lift[] = [];
  for (const r of rounds) {
    if (r.round === 0) continue;
    const c = leadingArm(r);
    const lift = c ? liftOf(c.vs_reference) : null;
    if (!c || !lift) continue;
    out.push({
      round: r.round,
      lift: lift.value,
      lo: lift.ci_lo,
      hi: lift.ci_hi,
      side: lift.side,
      label: c.arm.label,
      advance: r.overlap.advance,
    });
  }
  return out;
}

function verdictWord(d: Lift): { tone: "success" | "accent"; word: string } {
  return {
    tone: d.advance === "advanced" ? "success" : "accent",
    word: ROUND_ADVANCE_LABELS[d.advance],
  };
}

const STROKE: Record<LiftSide, string> = {
  above: "var(--color-success)",
  below: "var(--color-danger)",
  spans: "var(--color-text-secondary)",
};

const PRECISION_ADVICE: Record<NonNullable<ServedRound["panel_precision_verdict"]>, string> = {
  noise: "The panel is re-reading its own noise — sharpen the cells before buying more of them.",
  spread: "The cells genuinely differ — that spread is signal about where this optimizer prompt works.",
};

function LiftRow({ d, x }: { d: Lift; x: (v: number) => number }) {
  const stroke = STROKE[d.side];
  const value = `${fmtSigned(d.lift)} [${fmtSigned(d.lo)}, ${fmtSigned(d.hi)}]`;
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
        aria-label={`Round ${d.round}: ${value}, ${ROUND_ADVANCE_LABELS[d.advance]}`}
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
  const { dash } = useCycleStream();
  const rounds = useMemo(() => dash?.rounds ?? [], [dash?.rounds]);
  const lifts = useMemo(() => liftsOf(rounds), [rounds]);

  // ONE axis across rounds: per-round auto-scaling hides the tightening.
  const x = useMemo(() => {
    const vals = [0, ...lifts.flatMap((d) => [d.lo, d.hi])];
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = (hi - lo || 1) * 0.1;
    const [d0, d1] = [lo - pad, hi + pad];
    return (v: number) => 4 + ((v - d0) / (d1 - d0)) * (AXIS_W - 8);
  }, [lifts]);

  const latest = lifts.length ? lifts[lifts.length - 1] : null;
  const precise = useMemo(
    () => rounds.filter((r) => r.round > 0 && r.panel_precision).at(-1) ?? null,
    [rounds],
  );
  const precision = precise?.panel_precision ?? null;
  const advice = precise?.panel_precision_verdict;

  return (
    <CardFrame title="Outer signal" headingTag="h2">
      {!latest ? (
        <p className="note-empty">
          {rounds.some((r) => r.round > 0)
            ? "No round has two cells both its leading arm and the origin measured, so no interval can be drawn. A one-cell panel never will — the reading is honest, not missing."
            : "Select a pp-self cycle with a completed round to see whether its panel resolves anything yet."}
        </p>
      ) : (
        <>
          <p className="note-lede">
            <Badge tone={verdictWord(latest).tone}>
              {verdictWord(latest).word}
            </Badge>{" "}
            Round {latest.round}&rsquo;s leading arm lifts <strong>{fmtSigned(latest.lift)}</strong> [
            {fmtSigned(latest.lo)}, {fmtSigned(latest.hi)}] over its parent, on the cells both measured.
            {latest.side === "spans"
              ? " The interval spans 0 — this panel cannot yet tell that arm from its parent, and the point estimate above should not be read as a win."
              : ""}
          </p>
          <div className="ov-forest">
            {lifts.map((d) => (
              <LiftRow key={d.round} d={d} x={x} />
            ))}
          </div>
          {precision ? (
            <p className="note-lede">
              Each cell was measured to ±{precision.estimation_sd.toFixed(3)} logits; the cells
              landed ±{precision.observed_sd.toFixed(3)} apart across {precision.n_cells}.
              {advice ? ` ${PRECISION_ADVICE[advice]}` : ""}
            </p>
          ) : null}
        </>
      )}
    </CardFrame>
  );
});

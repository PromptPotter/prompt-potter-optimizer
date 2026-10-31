"use client";

import { memo, useMemo } from "react";
import { useCycleStream } from "@/lib/poll";
import type { RoundAdvance, ServedRound } from "@/lib/api/types";
import { PRECISION_VERDICT_ADVICE, ROUND_ADVANCE_INFO } from "@/lib/api/types.generated";
import { CardFrame, Badge, Empty, Term } from "@/components/ui";
import { liftOf, type LiftSide } from "@/lib/derivations";
import { fmtInterval, fmtLogits, fmtUnit } from "@/lib/format";

const AXIS_W = 220;
const ROW_H = 18;

type Lift = {
  round: number;
  lift: number;
  lo: number;
  hi: number;
  side: LiftSide;
  label: string;
  advance: RoundAdvance;
};

// served: `overlap.lead` is the pick over C0 on the origin panel, the one reading `advance` judges.
function liftsOf(rounds: ServedRound[]): Lift[] {
  const out: Lift[] = [];
  for (const r of rounds) {
    if (r.round === 0) continue;
    const { lead, advance } = r.overlap;
    const lift = liftOf(lead);
    const label = lead.b?.address.arm?.label;
    if (!lift || !label) continue;
    out.push({
      round: r.round,
      lift: lift.value,
      lo: lift.ci_lo,
      hi: lift.ci_hi,
      side: lift.side,
      label,
      advance,
    });
  }
  return out;
}

function AdvanceBadge({ advance }: { advance: RoundAdvance }) {
  const { label, sentence } = ROUND_ADVANCE_INFO[advance];
  return (
    <Term content={sentence}>
      <Badge tone={advance === "advanced" ? "success" : "accent"}>{label}</Badge>
    </Term>
  );
}

const STROKE: Record<LiftSide, string> = {
  above: "var(--color-success)",
  below: "var(--color-danger)",
  spans: "var(--color-text-secondary)",
};

function LiftRow({ d, x }: { d: Lift; x: (v: number) => number }) {
  const stroke = STROKE[d.side];
  const value = `${fmtUnit("delta", d.lift)} ${fmtInterval("delta", d.lo, d.hi)}`;
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
        aria-label={`Round ${d.round}: ${value}, ${ROUND_ADVANCE_INFO[d.advance].label}`}
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

  // Before a round completes there is no signal to show, and the time-ray already says a round runs.
  if (!rounds.some((r) => r.round > 0)) return null;

  return (
    <CardFrame title="Outer signal" headingTag="h2" span="full">
      {!latest ? (
        <Empty>
          No round&apos;s pick was read against the origin on the origin panel, so no interval can be
          drawn.
        </Empty>
      ) : (
        <>
          <p className="note-lede">
            <AdvanceBadge advance={latest.advance} /> Round {latest.round}&rsquo;s pick{" "}
            {latest.label} lifts <strong>{fmtUnit("delta", latest.lift)}</strong>{" "}
            {fmtInterval("delta", latest.lo, latest.hi)} over the origin, on the origin panel.
          </p>
          <div className="ov-forest">
            {lifts.map((d) => (
              <LiftRow key={d.round} d={d} x={x} />
            ))}
          </div>
          {precision ? (
            <p className="note-lede">
              Each cell was measured to ±{fmtLogits(precision.estimation_sd)}; the cells landed ±
              {fmtLogits(precision.observed_sd)} apart across {precision.n_cells}.
              {advice ? ` ${PRECISION_VERDICT_ADVICE[advice]}` : ""}
            </p>
          ) : null}
        </>
      )}
    </CardFrame>
  );
});

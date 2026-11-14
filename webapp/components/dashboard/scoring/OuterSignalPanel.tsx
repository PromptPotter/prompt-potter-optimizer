import { memo, useMemo } from "react";
import { useViewedDashboard } from "@/lib/state/poll";
import {
  type MeasuredLift,
  type RoundAdvance,
  type ServedRound,
  PRECISION_VERDICT_ADVICE,
  ROUND_ADVANCE_INFO,
} from "@/lib/api/types";
import {
  CardFrame,
  Badge,
  Empty,
  IntervalRow,
  intervalDomain,
  Term,
  type IntervalDomain,
} from "@/components/ui";
import { cx } from "@/lib/cx";
import { liftOf, type LiftSide } from "@/lib/derivations";
import { fmtLogits, sideTone } from "@/lib/format";

type Lift = {
  round: number;
  estimate: MeasuredLift["estimate"];
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
      estimate: lift.estimate,
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
      <Badge tone={advance === "advanced" ? "success" : "attention"}>{label}</Badge>
    </Term>
  );
}

function LiftRow({ d, domain, latest }: { d: Lift; domain: IntervalDomain; latest: boolean }) {
  const value = d.estimate.text;
  return (
    <IntervalRow
      className={cx(sideTone(d.side), latest && "ov-latest")}
      label={`r${d.round}`}
      title={`${d.label} — round ${d.round}`}
      value={value}
      domain={domain}
      point={d.estimate.value}
      lo={d.estimate.ci_lo}
      hi={d.estimate.ci_hi}
      reference={0}
      ariaLabel={`Round ${d.round}: ${value}, ${ROUND_ADVANCE_INFO[d.advance].label}`}
    />
  );
}

export const OuterSignalPanel = memo(function OuterSignalPanel() {
  const { dash } = useViewedDashboard();
  const rounds = useMemo(() => dash?.rounds ?? [], [dash?.rounds]);
  const lifts = useMemo(() => liftsOf(rounds), [rounds]);

  // ONE axis across rounds, zero on it: per-round auto-scaling hides the tightening.
  const domain = useMemo(
    () => intervalDomain([0, ...lifts.flatMap((d) => [d.estimate.ci_lo ?? d.estimate.value, d.estimate.ci_hi ?? d.estimate.value])]),
    [lifts],
  );

  const latest = lifts.length ? lifts[lifts.length - 1] : null;
  const precise = useMemo(
    () => rounds.filter((r) => r.round > 0 && r.panel_precision).at(-1) ?? null,
    [rounds],
  );
  const precision = precise?.panel_precision ?? null;
  const advice = precise?.panel_precision_verdict;
  // served: the noise walk, the generator's lift prior off the latest round's own arm-vs-parent pairs.
  const walked = rounds.filter((r) => r.lift_prior).at(-1) ?? null;
  const held = rounds.filter((r) => r.hold_up).at(-1) ?? null;

  // Before a round completes there is no signal to show, and the time-ray already says a round runs.
  if (!rounds.some((r) => r.round > 0)) return null;

  return (
    <CardFrame title="Outer signal" headingTag="h2" span="full">
      {!latest || domain === null ? (
        <Empty>
          No round&apos;s pick was read against the origin on the origin panel, so no interval can be
          drawn.
        </Empty>
      ) : (
        <>
          <p className="note-lede">
            <AdvanceBadge advance={latest.advance} /> Round {latest.round}&rsquo;s pick{" "}
            {latest.label} lifts <strong>{latest.estimate.text}</strong>
 over the origin, on the origin
            panel.
          </p>
          <div className="ov-interval-rows">
            {lifts.map((d) => (
              <LiftRow key={d.round} d={d} domain={domain} latest={d === latest} />
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
      {walked?.lift_prior ? (
        <p className="note-lede">
          Round {walked.round}&rsquo;s arms lift μ0 {fmtLogits(walked.lift_prior.mu0)} over their
          parent, τ {fmtLogits(walked.lift_prior.tau)} across {walked.lift_prior.pairs} pairs.
          {held?.hold_up
            ? ` Round ${held.round}'s promotion holds ${held.hold_up.text} over the parent it beat.`
            : ""}
        </p>
      ) : null}
    </CardFrame>
  );
});

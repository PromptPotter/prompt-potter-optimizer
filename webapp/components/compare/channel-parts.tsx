import {
  type HeadToHeadRow,
  type MeteredSpend,
  type MeasuredLift,
  READING_STATE_INFO,
  CEILING_METER_LABELS,
  COST_ABSENCE_WORDS,
  STOP_REASON_INFO,
} from "@/lib/api/types";
import { GuardBadge } from "./GuardBadge";
import {
  liftOf,
  meteredBucketsLine,
  ratePricedText,
  readPaired,
  spendHeadline,
  type LineStep,
} from "@/lib/derivations";
import { cx } from "@/lib/cx";
import {
  sideTone,
  fmtDuration,
  fmtLift,
  fmtUnit,
  fmtUsd,

  PLACEHOLDER,
} from "@/lib/format";
import { Badge, Term } from "@/components/ui";

interface MetricFigure {
  value: string;
  band: string;
  tone?: string;
}

function StepLift({ lift }: { lift: MeasuredLift | null }) {
  if (lift === null) return null;
  return <span className={sideTone(lift.side)}> {lift.estimate.point}</span>;
}

export function MainLine({ steps }: { steps: readonly LineStep[] }) {
  if (steps.length === 0) return <span className="l4-dim">—</span>;
  return (
    <ol className="cmp-line">
      {steps.map((s) => {
        if (s.kind === "held") {
          return (
            <li key={`held-${s.round}`} className="cmp-line-step is-held">
              <span className="cmp-line-round">R{s.round}</span>
              <span className="cmp-line-label">held</span>
              <span className="cmp-line-change">kept its parent</span>
            </li>
          );
        }
        const { reading } = s.node.facts;
        return (
          <li
            key={s.node.address}
            className={cx("cmp-line-step", reading.election.selected && "is-crowned")}
          >
            <span className="cmp-line-round">R{reading.arm.round}</span>
            <span className="cmp-line-label">{s.node.label}</span>
            <span className="cmp-line-score">
              {reading.own?.fitness?.point ?? PLACEHOLDER}
              <StepLift lift={liftOf(reading.vs_reference)} />
            </span>
            <span className="cmp-line-change" title={reading.changes_description || undefined}>
              {reading.arm.round === 0 ? "origin" : reading.changes_description || PLACEHOLDER}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function Metric({ label, value, band, tone }: MetricFigure & { label?: string }) {
  return (
    <div className={cx("cmp-metric", tone)}>
      {label && <span className="cmp-metric-label">{label}</span>}
      <span className="cmp-metric-num l4-effect-mean">{value}</span>
      <span className="cmp-metric-band" title={band}>
        {band}
      </span>
    </div>
  );
}

export function benchLead(row: HeadToHeadRow | null): {
  score: MetricFigure;
  lift: MetricFigure;
} {
  const bench = row?.bench ?? null;
  const s = bench?.selected ?? null;
  const level = s?.[s.headline] ?? null;
  const lift = bench === null ? null : readPaired(bench.vs_origin);
  return {
    score:
      bench === null || s === null
        ? {
            value: PLACEHOLDER,
            band: bench === null ? "" : READING_STATE_INFO[bench.status.state].sentence,
          }
        : {
            value: level?.point ?? PLACEHOLDER,
            band: `${s.headline} ${level?.band ?? PLACEHOLDER} · ${s.n}/${bench.bench_size} rows`,
          },
    lift:
      lift === null
        ? { value: PLACEHOLDER, band: "" }
        : lift.read
          ? {
              value: lift.lift.estimate.point,
              band: lift.lift.estimate.band ?? PLACEHOLDER,
              tone: sideTone(lift.lift.side),
            }
          : { value: lift.label, band: lift.sentence },
  };
}

export function SpentReading({ metered }: { metered: MeteredSpend | null }) {
  if (metered === null) return PLACEHOLDER;
  const priced = ratePricedText(metered.rate_priced_usd, metered.calls_rate_priced);
  const sub =
    `${priced ? `${priced} · ` : ""}Incurred ${fmtUsd(metered.incurred_usd)} · ` +
    `Counted here against the campaign cap ${fmtUsd(metered.metered_usd)} ` +
    `${CEILING_METER_LABELS[metered.meter]}: ${meteredBucketsLine(metered)}`;
  return (
    <span className="cmp-metric">
      {spendHeadline(metered)}
      <span className="cmp-metric-band" title={sub}>
        {sub}
      </span>
    </span>
  );
}

function ElectedOn({ election }: { election: HeadToHeadRow["election"] }) {
  if (!election) return PLACEHOLDER;
  const ability = election.ability;
  if (election.reads_on === "ability" && ability) {
    return ability.theta?.point ?? "θ unread";

  }
  if (election.reads_on === election.elects_on) return election.reads_on;
  return `${election.reads_on} · θ ${ability?.caveat ?? "unread"}`;
}

function LiftPerUsd({ bench }: { bench: HeadToHeadRow["bench"] }) {
  const { lift_per_usd: per, absent } = bench.cost;
  const lift = liftOf(bench.vs_origin);
  if (absent !== null) return COST_ABSENCE_WORDS[absent];
  return per === null || lift === null ? PLACEHOLDER : `${fmtLift(lift.measurand.unit, per)} per $`;
}

export function HeadlineFacts({ row }: { row: HeadToHeadRow }) {
  return (
    <dl className="cmp-channel-facts cmp-channel-headline">
      <div>
        <dt>
          <Term
            content={
              <p className="cmp-line-key">
                The column the pick&rsquo;s round elected it on, and the one to quote it on: a pick
                won on ability is quoted on θ unless its θ carries a caveat — there θ is not
                ability, and the election reads on fitness.
              </p>
            }
          >
            elected on
          </Term>
        </dt>
        <dd>
          <ElectedOn election={row.election} />
        </dd>
      </div>
      {row.swapped_in.length > 0 && (
        <div>
          <dt>swapped in</dt>
          <dd>{row.swapped_in.join(", ")}</dd>
        </div>
      )}
      <div>
        <dt>lift / USD</dt>
        <dd>
          <LiftPerUsd bench={row.bench} />
        </dd>
      </div>
      <div>
        <dt>spent</dt>
        <dd>
          <SpentReading metered={row.spend_metered} />
        </dd>
      </div>
      <div>
        <dt>worked</dt>
        <dd>{fmtDuration(row.worked_s)}</dd>
      </div>
      <div>
        <dt>calls</dt>
        <dd>{row.calls ?? PLACEHOLDER}</dd>
      </div>
      <div>
        <dt>replayed</dt>
        <dd>{fmtUnit("rate", row.spend_metered?.replay_share)}</dd>
      </div>
    </dl>
  );
}

export function HeadlineBadges({ row }: { row: HeadToHeadRow }) {
  return (
    <span className="cmp-channel-badges">
      <GuardBadge guard={row.guard} />
      {row.bench_set === null && <Badge>no headline</Badge>}
      {row.status.mark === "failed" && row.status.stop_reason !== null && (
        <Badge tone="danger">{STOP_REASON_INFO[row.status.stop_reason].label}</Badge>
      )}
    </span>
  );
}

export function Swatch({ ink }: { ink: string | null }) {
  if (ink === null) return null;
  return <span className="cmp-swatch" style={{ background: ink }} aria-hidden="true" />;
}

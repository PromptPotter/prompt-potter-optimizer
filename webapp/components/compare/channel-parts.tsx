import { CELL_MEAN_ROWS } from "@/lib/cell-means";
import type { HeadToHeadRow, MeteredSpend } from "@/lib/api";
import type { LiftEstimate } from "@/lib/api/types";
import { GuardBadge } from "@/components/shell/GuardBadge";
import { liftOf, pathOf, readPaired, spendStat, type LineStep } from "@/lib/derivations";
import { cx } from "@/lib/cx";
import {
  sideTone,
  fmtDuration,
  fmtMetricInterval,
  fmtMetricValue,
  fmtPct0,
  fmtSigned,
} from "@/lib/format";
import { encodeCyclePath } from "@/lib/ids";
import { Badge } from "@/components/ui";

interface MetricFigure {
  value: string;
  band: string;
  tone?: string;
}

function StepLift({ lift }: { lift: LiftEstimate | null }) {
  if (lift === null) return null;
  return <span className={sideTone(lift.side)}> {fmtSigned(lift.value, 2)}</span>;
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
        const { reading } = s.node;
        return (
          <li
            key={`${encodeCyclePath(pathOf(s.node))}/${s.node.id}`}
            className={cx("cmp-line-step", reading.election.selected && "is-crowned")}
          >
            <span className="cmp-line-round">R{reading.arm.round}</span>
            <span className="cmp-line-label">{s.node.label}</span>
            <span className="cmp-line-score">
              {fmtPct0(reading.own?.accuracy?.value ?? null)}
              <StepLift lift={liftOf(reading.vs_reference)} />
            </span>
            <span className="cmp-line-change" title={reading.changes_description || undefined}>
              {reading.arm.round === 0 ? "origin" : reading.changes_description || "—"}
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
        ? { value: "—", band: bench?.status.sentence ?? "" }
        : {
            value: fmtMetricValue("level", level?.value ?? null),
            band: `${s.headline} ${fmtMetricInterval("level", level?.ci_lo ?? null, level?.ci_hi ?? null)} · ${s.n}/${bench.bench_size} rows`,
          },
    lift:
      lift === null
        ? { value: "—", band: "" }
        : lift.read
          ? {
              value: fmtSigned(lift.lift.estimate.value),
              band: fmtMetricInterval(
                "delta",
                lift.lift.estimate.ci_lo,
                lift.lift.estimate.ci_hi,
              ),
              tone: sideTone(lift.lift.estimate.side),
            }
          : { value: lift.label, band: lift.sentence },
  };
}

export function SpentReading({ metered }: { metered: MeteredSpend | null }) {
  if (metered === null) return "—";
  const { value, sub } = spendStat(metered);
  return (
    <span className="cmp-metric">
      {value}
      <span className="cmp-metric-band" title={sub}>
        {sub}
      </span>
    </span>
  );
}

export function CellMeans({ means }: { means: Record<string, number> | undefined }) {
  const rows = CELL_MEAN_ROWS.flatMap((r) => {
    const v = means?.[r.key];
    return v === undefined ? [] : [{ ...r, v }];
  });
  if (rows.length === 0) return null;
  return (
    <dl className="cmp-channel-facts cmp-channel-means">
      {rows.map((r) => (
        <div key={r.key}>
          <dt>{r.label}</dt>
          <dd>{r.fmt(r.v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CostFacts({ row }: { row: HeadToHeadRow }) {
  return (
    <dl className="cmp-channel-facts cmp-channel-cost">
      <div>
        <dt>lift / USD</dt>
        <dd>{fmtSigned(row.bench.cost.lift_per_usd, 2)}</dd>
      </div>
      <div>
        <dt>spent</dt>
        <dd>
          <SpentReading metered={row.spend_metered} />
        </dd>
      </div>
      <div>
        <dt>worked</dt>
        <dd>{row.worked_s === null ? "—" : fmtDuration(row.worked_s)}</dd>
      </div>
      <div>
        <dt>calls</dt>
        <dd>{row.calls ?? "—"}</dd>
      </div>
      <div>
        <dt>replayed</dt>
        <dd>{row.spend_metered === null ? "—" : fmtPct0(row.spend_metered.replay_share)}</dd>
      </div>
    </dl>
  );
}

export function HeadlineBadges({ row }: { row: HeadToHeadRow }) {
  return (
    <span className="cmp-channel-badges">
      <GuardBadge guard={row.guard} />
      {row.bench_set === null && <Badge>no headline</Badge>}
      {row.status.mark === "failed" && <Badge tone="danger">{row.status.label}</Badge>}
    </span>
  );
}

export function Swatch({ ink }: { ink: string | null }) {
  if (ink === null) return null;
  return <span className="cmp-swatch" style={{ background: ink }} aria-hidden="true" />;
}

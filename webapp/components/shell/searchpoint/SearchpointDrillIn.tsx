"use client";

import type { ReactNode } from "react";
import type { ArmReading, BenchPassProgress } from "@/lib/api/types";
import type { NodeSchemaReading } from "@/lib/types";
import { readPaired, type ObserveConfig } from "@/lib/derivations";
import { TERMS } from "@/lib/terms";
import { NOT_SEPARABLE } from "@/lib/fitness";
import { Term } from "@/components/ui";
import { fmtFitness, fmtPct1, fmtSigned, fmtThetaSe, fmtTokens } from "@/lib/format";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";

export function SearchpointDrillIn({
  reading,
  cfg,
  benchPass,
  stats,
  measurements,
  arms,
  schema,
  pending,
  overlay,
  onOverlay,
  actions,
}: {
  reading: ArmReading | null;
  cfg: ObserveConfig | null;
  benchPass?: BenchPassProgress | null;
  stats?: ReactNode;
  measurements?: ReactNode;
  // `null` = the host cannot count the round's arms, a different fact from one.
  arms: number | null;
  schema: NodeSchemaReading;
  pending: string;
  overlay?: Record<string, unknown>;
  onOverlay?: (next: Record<string, Record<string, unknown>>) => void;
  actions?: React.ReactNode;
}) {
  const accuracy = reading?.own?.accuracy?.value ?? null;
  const composite = reading?.own?.composite?.value ?? null;
  const bench = reading?.bench ?? null;
  const pair = reading?.vs_reference ? readPaired(reading.vs_reference) : null;
  const paired = pair?.read ? pair.lift : null;
  const lift = paired?.estimate ?? null;
  return (
    <>
      {stats}
      {cfg ? (
        <NodeSurface
          node={null}
          point={{ origin_prompt_fields: cfg.promptFields, pipeline_overlay: {} }}
          overlay={overlay ?? cfg.config}
          schema={schema}
          mode="values"
          onConfigChange={onOverlay}
        />
      ) : (
        <p className="inspector-note">{pending}</p>
      )}
      <div className="inspector-body">
        {reading === null ? (
          <div className="inspector-note">{pending}</div>
        ) : (
          <>
            <Fact k="label" v={reading.arm.label} />
            {accuracy != null && (
              <Fact
                k="accuracy"
                v={
                  reading.panel.scored != null
                    ? `${fmtPct1(accuracy)} of ${reading.panel.scored}`
                    : fmtPct1(accuracy)
                }
              />
            )}
            {(benchPass != null || bench != null) && (
              <Fact
                k="bench · held out"
                hint="This same searchpoint on the held-out bench set: questions no round of the search ever read, so no candidate was chosen on them. A different set of questions from the accuracy above, so the two rates are not differenced. Any bench lift is against the ORIGIN, not the parent."
                v={
                  benchPass != null
                    ? `${benchPass.accuracy == null ? "—" : fmtPct1(benchPass.accuracy)} so far · ${benchPass.scored} of ${benchPass.rows} in flight`
                    : bench?.level == null
                      ? `— of ${bench?.n}`
                      : `${fmtPct1(bench.level.value)} of ${bench.n}${
                          bench.level.ci_lo != null && bench.level.ci_hi != null
                            ? ` [${fmtPct1(bench.level.ci_lo)}, ${fmtPct1(bench.level.ci_hi)}]`
                            : ""
                        }`
                }
              />
            )}
            {paired != null && (
              <Fact
                k="vs parent"
                v={
                  paired.measurand.unit === "rate"
                    ? fmtPct1(paired.rate_a)
                    : fmtFitness(paired.rate_a)
                }
                hint="The candidate's PARENT — the origin at round 0, the prior round's winner after — re-scored on the samples THIS candidate measured, and the floor the promotion gate compared it against. Under elimination a candidate may run only part of the round's samples, so the parent's full-set rate is the wrong comparison and would read as a phantom lift."
              />
            )}
            {lift != null && (
              <Fact
                k="lift vs parent"
                hint="Mean per-cell (candidate − parent) across the cells both measured, Student-t bracketed. Pairing removes the parent's cell-to-cell variation, so this is sharper than the candidate's own mean band."
                v={
                  <>
                    {fmtSigned(lift.value)} [{fmtSigned(lift.ci_lo)}, {fmtSigned(lift.ci_hi)}]
                    {lift.side !== "spans" ? (
                      " clears 0"
                    ) : (
                      <span className="l4-eff-flat"> — {NOT_SEPARABLE}</span>
                    )}
                  </>
                }
              />
            )}
            {reading.ability?.theta != null && (
              <Fact
                k="ability θ"
                hint="Difficulty-adjusted Rasch ability — the metric the round winner is elected on. Clearing harder samples is worth more than more wins on easy ones, so a higher θ can beat a higher accuracy."
                v={fmtThetaSe(reading.ability.theta, reading.ability.se)}
              />
            )}
            {composite != null && <Fact k="composite" v={composite.toFixed(4)} />}
            <Fact
              k="winner"
              v={!reading.election.selected ? "no" : arms === 1 ? "yes — uncontested" : "yes"}
            />
            {/* "measured on", never "cost": BACKEND spend alone — judge and optimizer carry no candidate. */}
            {reading.spend != null && (
              <>
                <Fact
                  k="measured on"
                  v={`${fmtTokens(reading.spend.input_tokens)} in · ${fmtTokens(reading.spend.output_tokens)} out${
                    reading.panel.cached ? ` · ${reading.panel.cached} replayed` : ""
                  }`}
                  hint={TERMS.cache_replayed}
                />
                <Fact k="prefix" v={reading.spend.prefix.badge} hint={TERMS.cache_prefix} />
              </>
            )}
          </>
        )}
      </div>
      {measurements && <div className="inspector-samples">{measurements}</div>}
      {actions && <div className="inspector-actions">{actions}</div>}
    </>
  );
}

function Fact({ k, v, hint }: { k: string; v: React.ReactNode; hint?: string }) {
  return (
    <div className="inspector-row">
      <span className="inspector-key">{k}</span>
      {hint ? (
        <Term className="inspector-val" content={hint}>
          {v}
        </Term>
      ) : (
        <span className="inspector-val">{v}</span>
      )}
    </div>
  );
}

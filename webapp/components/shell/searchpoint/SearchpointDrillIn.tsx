import type { ReactNode } from "react";
import {
  type ArmReading,
  type VerificationMark,
  PREFIX_STATE_TITLES,
  READING_STATE_INFO,
  type ReadingState,
} from "@/lib/api/types";
import type { NodeSchemaReading } from "@/lib/types";
import type { ReadFailure } from "@/lib/read-cache";
import { liftOf, NOT_SEPARABLE, type ObserveConfig } from "@/lib/derivations";
import { FailureLine, Said, Term } from "@/components/ui";
import {
  fmtLevelBand,
  fmtLiftBand,
  fmtThetaSe,
  fmtUnit,
  PLACEHOLDER,
  sideTone,
} from "@/lib/format";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";

export function SearchpointDrillIn({
  reading,
  cfg,
  benchPass,
  stats,
  measurements,
  schema,
  pending,
  failure = null,
  overlay,
  onOverlay,
  actions,
}: {
  reading: ArmReading | null;
  cfg: ObserveConfig | null;
  // The arm's bench pass while it is in flight, or the one that failed; a read one is `reading.bench`.
  benchPass?: VerificationMark | null;
  stats?: ReactNode;
  measurements?: ReactNode;
  schema: NodeSchemaReading;
  pending: string;
  // The failed read `cfg` and `reading` come from: said in place of `pending`.
  failure?: ReadFailure | null;
  overlay?: Record<string, unknown>;
  onOverlay?: (next: Record<string, Record<string, unknown>>) => void;
  actions?: React.ReactNode;
}) {
  const accuracy = reading?.own?.accuracy ?? null;
  const composite = reading?.own?.composite?.value ?? null;
  const bench = reading?.bench ?? null;
  const paired = liftOf(reading?.vs_reference ?? null);
  // Said once, in the first slot left empty.
  const note =
    failure !== null ? (
      <FailureLine failure={failure} lead="Could not read this searchpoint." className="inspector-note" />
    ) : (
      <p className="inspector-note">{pending}</p>
    );
  return (
    <>
      {stats}
      {cfg ? (
        <NodeSurface
          node={null}
          point={{ origin_prompts: cfg.prompts, pipeline_overlay: {} }}
          overlay={overlay ?? cfg.config}
          schema={schema}
          mode="values"
          onConfigChange={onOverlay}
        />
      ) : (
        note
      )}
      <div className="inspector-body">
        {reading === null ? (
          cfg && note
        ) : (
          <>
            <Fact k="label" v={reading.arm.label} />
            {accuracy != null && (
              <Fact
                k="accuracy"
                v={`${fmtLevelBand("rate", accuracy)}${onPanel(reading.panel)}`}
              />
            )}
            {(benchPass != null || bench != null) && (
              <Fact
                k="bench · held out"
                hint="This same searchpoint on the held-out bench set: questions no round of the search ever read, so no candidate was chosen on them. A different set of questions from the accuracy above, so the two rates are not differenced. Any bench lift is against the ORIGIN, not the parent."
                v={
                  benchPass?.failed_as != null
                    ? <FailedPass state={benchPass.failed_as} />
                    : benchPass != null
                    ? `${fmtUnit("rate", benchPass.level?.value)} so far · ${benchPass.scored} of ${benchPass.rows} in flight`
                    : bench?.level == null
                      ? `${PLACEHOLDER} of ${bench?.n}`
                      : `${fmtLevelBand("rate", bench.level)} of ${bench.n}`
                }
              />
            )}
            {paired != null && (
              <Fact
                k="vs parent"
                v={fmtUnit(paired.measurand.unit, paired.rate_a)}
                hint="The candidate's PARENT — the origin at round 0, the prior round's winner after — re-scored on the samples THIS candidate measured, and the floor the promotion gate compared it against. Under elimination a candidate may run only part of the round's samples, so the parent's full-set rate is the wrong comparison and would read as a phantom lift."
              />
            )}
            {paired != null && (
              <Fact
                k="lift vs parent"
                hint="Mean per-cell (candidate − parent) across the cells both measured, Student-t bracketed. Pairing removes the parent's cell-to-cell variation, so this is sharper than the candidate's own mean band."
                v={
                  <>
                    {fmtLiftBand(paired.measurand.unit, paired.estimate)}
                    {paired.estimate.side !== "spans" ? (
                      " clears 0"
                    ) : (
                      <span className={sideTone(null)}> — {NOT_SEPARABLE}</span>
                    )}
                  </>
                }
              />
            )}
            {reading.ability?.theta != null && (
              <Fact
                k="ability"
                hint="Difficulty-adjusted Rasch ability — the metric the round winner is elected on. Clearing harder samples is worth more than more wins on easy ones, so a higher θ can beat a higher accuracy."
                v={fmtThetaSe(reading.ability.theta, reading.ability.se)}
              />
            )}
            {composite != null && <Fact k="composite" v={fmtUnit("score", composite)} />}
            <Fact
              k="winner"
              v={
                !reading.election.held
                  ? "not yet elected"
                  : !reading.election.selected
                    ? "no"
                    : reading.election.crown === "uncontested"
                      ? "yes — uncontested"
                      : "yes"
              }
            />
            {/* "measured on", never "cost": BACKEND spend alone — judge and optimizer carry no candidate. */}
            {reading.tokens != null && (
              <Fact
                k="measured on"
                v={`${fmtUnit("tokens", reading.tokens.input)} in · ${fmtUnit("tokens", reading.tokens.output)} out${
                  reading.panel.cached ? ` · ${reading.panel.cached} replayed` : ""
                }`}
                hint={PREFIX_STATE_TITLES.replayed}
              />
            )}
            {reading.prefix != null && (
              <Fact
                k="prefix"
                v={reading.prefix.badge}
                hint={PREFIX_STATE_TITLES[reading.prefix.state]}
              />
            )}
          </>
        )}
      </div>
      {measurements && <div className="inspector-samples">{measurements}</div>}
      {actions && <div className="inspector-actions">{actions}</div>}
    </>
  );
}

// A pass that read nothing, as its served row words it: a fault sounds, any other reason stays quiet.
function FailedPass({ state }: { state: ReadingState }) {
  const { label, sentence, fault } = READING_STATE_INFO[state];
  return <Said tone={fault ? "danger" : "quiet"} label={label} sub={sentence} />;
}

// "on 12 of 20" where the walk stopped short of its length.
function onPanel({ scored, expected }: ArmReading["panel"]): string {
  if (scored == null) return "";
  return expected != null && expected !== scored ? ` on ${scored} of ${expected}` : ` on ${scored}`;
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

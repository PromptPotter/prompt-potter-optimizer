"use client";
// What one searchpoint is and what it scored — chrome, shown on the dashboard and on Records.
// Presentational: the host resolves row and spec, since only the dashboard holds a live stream.

import type { ReactNode } from "react";
import type { ElectedRow, PipelineStatus } from "@/lib/types";
import type { NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import { cacheShare, prefixReading, type ObserveConfig } from "@/lib/derivations";
import { TERMS } from "@/lib/terms";
import { NOT_SEPARABLE, liftSeparates } from "@/lib/fitness";
import { Term } from "@/components/ui";
import { fmtPct1, fmtSigned, fmtTokens } from "@/lib/format";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";

export function SearchpointDrillIn({
  row,
  cfg,
  measurements,
  arms,
  schema,
  schemaStatus,
  outputSchema,
  pending,
  overlay,
  onOverlay,
  actions,
}: {
  // `null` also where a round still scoring has written no document yet.
  row: ElectedRow | null;
  cfg: ObserveConfig | null;
  measurements?: ReactNode;
  // `null` = the host cannot count the round's arms, a different fact from one.
  arms: number | null;
  schema: Record<string, NodeConfigParam[]> | null;
  schemaStatus: PipelineStatus;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
  pending: string;
  overlay?: Record<string, unknown>;
  // Absence IS read-only. Emits the WHOLE running config — diff against the seed (`overlayEdits`).
  onOverlay?: (next: Record<string, Record<string, unknown>>) => void;
  actions?: React.ReactNode;
}) {
  return (
    <>
      {cfg ? (
        <NodeSurface
          node={null}
          point={{ origin_prompt_fields: cfg.promptFields, pipeline_overlay: {} }}
          overlay={overlay ?? cfg.config}
          schema={schema}
          schemaStatus={schemaStatus}
          outputSchema={outputSchema}
          // No `label`: both hosts already name the point in the line above.
          mode="values"
          onConfigChange={onOverlay}
        />
      ) : (
        <p className="inspector-note">{pending}</p>
      )}
      <div className="inspector-body">
        {row === null ? (
          <div className="inspector-note">{pending}</div>
        ) : (
          <>
            <Fact k="label" v={row.label} />
            {typeof row.accuracy === "number" && (
              <Fact
                k="accuracy"
                v={
                  typeof row.n_samples === "number"
                    ? `${fmtPct1(row.accuracy)} of ${row.n_samples}`
                    : fmtPct1(row.accuracy)
                }
              />
            )}
            {typeof row.matchedParentAccuracy === "number" && (
              <Fact
                k="vs parent"
                v={fmtPct1(row.matchedParentAccuracy)}
                hint="The candidate's PARENT — the origin at round 0, the prior round's winner after — re-scored on the samples THIS candidate measured, and the floor the promotion gate compared it against. Under elimination a candidate may run only part of the round's samples, so the parent's full-set rate is the wrong comparison and would read as a phantom lift."
              />
            )}
            {typeof row.matchedParentLift === "number" &&
              typeof row.matchedParentLiftCiLo === "number" &&
              typeof row.matchedParentLiftCiHi === "number" && (
                <Fact
                  k="lift vs parent"
                  hint="Mean per-cell (candidate − parent) across the cells both measured, Student-t bracketed. Pairing removes the parent's cell-to-cell variation, so this is sharper than the candidate's own mean band."
                  v={
                    <>
                      {fmtSigned(row.matchedParentLift)} [{fmtSigned(row.matchedParentLiftCiLo)},{" "}
                      {fmtSigned(row.matchedParentLiftCiHi)}]
                      {liftSeparates(row.matchedParentLiftCiLo, row.matchedParentLiftCiHi) ? (
                        " clears 0"
                      ) : (
                        <span className="l4-eff-flat"> — {NOT_SEPARABLE}</span>
                      )}
                    </>
                  }
                />
              )}
            {typeof row.theta === "number" && (
              <Fact
                k="ability θ"
                hint="Difficulty-adjusted Rasch ability — the metric the round winner is elected on. Clearing harder samples is worth more than more wins on easy ones, so a higher θ can beat a higher accuracy."
                v={`${row.theta.toFixed(2)}${
                  typeof row.theta_se === "number" ? ` ± ${row.theta_se.toFixed(2)}` : ""
                }`}
              />
            )}
            {typeof row.composite === "number" && (
              <Fact k="composite" v={row.composite.toFixed(4)} />
            )}
            <Fact
              k="winner"
              v={!row.is_winner ? "no" : arms === 1 ? "yes — uncontested" : "yes"}
            />
            {/* "measured on", never "cost": the BACKEND bucket alone — judge and optimizer spend
                carry no candidate. */}
            {typeof row.input_tokens === "number" && (
              <Fact
                k="measured on"
                v={`${fmtTokens(row.input_tokens)} in · ${fmtTokens(row.output_tokens ?? 0)} out${
                  row.cached_samples ? ` · ${row.cached_samples} replayed` : ""
                }`}
                hint={TERMS.cache_replayed}
              />
            )}
            {typeof row.input_tokens === "number" && (
              <Fact
                k="prefix"
                v={prefixReading(
                  cacheShare(row.cache_read_tokens, row.input_tokens, false),
                  false,
                ).label}
                hint={TERMS.cache_prefix}
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

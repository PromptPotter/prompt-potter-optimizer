"use client";
// WHAT one searchpoint is, and what it scored — the whole drill-in, wherever it is shown.
//
// It renders on the dashboard (a click on the candidates cladogram) and on Records (a click on a
// Compare channel's map), so it is chrome rather than either surface's own. A second copy would be
// a second answer to "what is this point", and the two would drift on exactly the thing that
// matters: which of these numbers the round could actually separate.
//
// **Presentational — it fetches nothing.** The two hosts read the same point from different
// places and neither can do the other's job: the dashboard holds a live snapshot for the ONE cycle
// it streams (`webapp/CLAUDE.md` § Polling shape allows exactly one), while a Compare channel may
// sit on any branch of any campaign and has only that branch's round file. So the host resolves
// the row, the spec and the samples, and hands them over.
//
// Every number here is SERVED. Nothing subtracts, ranks or re-scores: the lift is the election's
// own verdict with its own interval, not a difference of two accuracies computed in the browser.

import type { ElectedRow, SampleRow } from "@/lib/types";
import type { DraftPatch, NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import { cacheShare, prefixReading, type ObserveConfig } from "@/lib/derivations";
import { TERMS } from "@/lib/terms";
import { fmtPct1, fmtSigned, fmtTokens } from "@/lib/format";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { SampleRowItem, SAMPLE_RENDER_CAP } from "@/components/shell/samples/SampleRowItem";

export function SearchpointDrillIn({
  row,
  cfg,
  samples,
  arms,
  schema,
  outputSchema,
  pending,
  overlay,
  onOverlay,
  onApply,
  actions,
}: {
  // The point's served row. `null` while its source is still loading, or where no source holds
  // it — a round still scoring has written no document, and the drill-in says so rather than
  // rendering a table of dashes that reads like a measured zero.
  row: ElectedRow | null;
  // Its runnable specification, through the one observe join every spec surface reads.
  cfg: ObserveConfig | null;
  samples: readonly SampleRow[];
  // How many arms stood in this point's round. `null` = the host cannot say, which is a different
  // fact from one — a crown over no rivals is not an election.
  arms: number | null;
  schema: Record<string, NodeConfigParam[]> | null;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
  // What to say while there is no row: the two hosts are waiting on different things.
  pending: string;
  // What the config editor is SEEDED from — the point's resolved config, or a host's working copy
  // of it with the operator's changes written in. Same shape either way (`{node: {param: value}}`,
  // plus the pipeline's own non-node keys).
  overlay?: Record<string, unknown>;
  // **Absence IS read-only** — the same contract `NodeSurface` states, not a second flag beside
  // it. The emission is the point's WHOLE running config, never a delta: diff it against what was
  // seeded (`overlayEdits`) before reading it as "what changed".
  onOverlay?: (next: Record<string, Record<string, unknown>>) => void;
  onApply?: (patch: DraftPatch) => void;
  // Whatever this host lets the operator DO with the point — steer & fork, move a channel here.
  // The verbs differ per surface; the reading of the point does not.
  actions?: React.ReactNode;
}) {
  return (
    <>
      {cfg ? (
        <NodeSurface
          node={null}
          point={{ origin_prompt_fields: cfg.promptFields, pipeline_overlay: {} }}
          configSeed={overlay ?? cfg.config}
          schema={schema}
          outputSchema={outputSchema}
          // No `label`. NodeSurface prints one "because nothing else on screen names it", which is
          // true on the chat hero, where it carries the observe STATE ("best · C2.1"). Both hosts
          // of this drill-in name the point in the line directly above, so here it is the same
          // string twice.
          mode="values"
          onApply={onApply}
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
                title="The candidate's PARENT — the origin at round 0, the prior round's winner after — re-scored on the samples THIS candidate measured, and the floor the promotion gate compared it against. Under elimination a candidate may run only part of the round's samples, so the parent's full-set rate is the wrong comparison and would read as a phantom lift."
              />
            )}
            {/* The SERVED lift and its interval. A difference of two accuracies would be a number
                made in the browser (`webapp/CLAUDE.md` § Scoring authority) and would carry no
                uncertainty — so a margin the round could not resolve would render identically to
                one it could, which is the whole question this row answers. */}
            {typeof row.matchedParentLift === "number" &&
              typeof row.matchedParentLiftCiLo === "number" &&
              typeof row.matchedParentLiftCiHi === "number" && (
                <Fact
                  k="lift vs parent"
                  title="Mean per-cell (candidate − parent) across the cells both measured, Student-t bracketed. Pairing removes the parent's cell-to-cell variation, so this is sharper than the candidate's own mean band."
                  v={
                    <>
                      {fmtSigned(row.matchedParentLift)} [{fmtSigned(row.matchedParentLiftCiLo)},{" "}
                      {fmtSigned(row.matchedParentLiftCiHi)}]
                      {row.matchedParentLiftCiLo <= 0 && row.matchedParentLiftCiHi >= 0 ? (
                        <span className="l4-eff-flat"> spans 0 — not separable</span>
                      ) : (
                        " clears 0"
                      )}
                    </>
                  }
                />
              )}
            {typeof row.theta === "number" && (
              <Fact
                k="ability θ"
                title="Difficulty-adjusted Rasch ability — the metric the round winner is elected on. Clearing harder samples is worth more than more wins on easy ones, so a higher θ can beat a higher accuracy."
                v={`${row.theta.toFixed(2)}${
                  typeof row.theta_se === "number" ? ` ± ${row.theta_se.toFixed(2)}` : ""
                }`}
              />
            )}
            {typeof row.composite === "number" && (
              <Fact k="composite" v={row.composite.toFixed(4)} />
            )}
            {/* A crown over no rivals is not an election (`derivations/election.ts`): round 0 runs
                one arm. A null `arms` means the host cannot count them, so it reports the served
                fact rather than guessing at "uncontested". */}
            <Fact
              k="winner"
              v={!row.is_winner ? "no" : arms === 1 ? "yes — uncontested" : "yes"}
            />
            {/* What measuring THIS searchpoint consumed, and how much of it the provider served
                off its own prefix cache. Named "measured on" rather than "cost": it is the
                BACKEND bucket alone — the judge's spend carries no candidate and the optimizer's
                is per round — and a row labelled plain "cost" would silently mean one of three.
                Absent where nothing served an account (the Compare host's scoreboard source). */}
            {typeof row.input_tokens === "number" && (
              <Fact
                k="measured on"
                v={`${fmtTokens(row.input_tokens)} in · ${fmtTokens(row.output_tokens ?? 0)} out${
                  row.cached_samples ? ` · ${row.cached_samples} replayed` : ""
                }`}
                title={TERMS.cache_replayed}
              />
            )}
            {typeof row.input_tokens === "number" && (
              <Fact
                k="prefix"
                v={prefixReading(
                  cacheShare(row.cache_read_tokens, row.input_tokens, false),
                  false,
                ).label}
                title={TERMS.cache_prefix}
              />
            )}
          </>
        )}
      </div>
      {samples.length > 0 && (
        <div className="inspector-samples">
          <div className="rsv-group-head" aria-hidden>
            <span className="rsv-cand-label">{row?.label ?? ""} · samples</span>
            {/* Served numbers, not a tally over the rendered rows: the two disagree whenever this
                list is capped or still filling. */}
            {row && typeof row.accuracy === "number" && typeof row.n_samples === "number" && (
              <span className="rsv-tally">
                {(row.accuracy * 100).toFixed(0)}% of {row.n_samples}
              </span>
            )}
          </div>
          <div className="rsv-rows">
            {samples.slice(0, SAMPLE_RENDER_CAP).map((s) => (
              <SampleRowItem key={s.key} row={s} />
            ))}
            {samples.length > SAMPLE_RENDER_CAP && (
              <div className="rsv-empty-row">
                +{samples.length - SAMPLE_RENDER_CAP} more (rendering capped at{" "}
                {SAMPLE_RENDER_CAP}).
              </div>
            )}
          </div>
        </div>
      )}
      {actions && <div className="inspector-actions">{actions}</div>}
    </>
  );
}

// One key/value line of the stats block. `.inspector-row` is `display:contents`, so the pair lands
// on the grid the body owns rather than nesting a second one.
function Fact({
  k,
  v,
  title,
}: {
  k: string;
  v: React.ReactNode;
  title?: string;
}) {
  return (
    <div className="inspector-row">
      <span className="inspector-key">{k}</span>
      <span className="inspector-val" title={title}>
        {v}
      </span>
    </div>
  );
}

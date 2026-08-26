"use client";
import { useMemo, useState } from "react";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { fmtPct1, fmtSigned } from "@/lib/format";
import type { ElectedRow, SelectedCandidate } from "@/lib/types";
import { liveCandidateRow } from "@/lib/poll";
import {
  candidateObserveConfig,
  liveCandidateObserveConfig,
  samplesForRow,
} from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { Dialog } from "@/components/ui";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { SteerForkPanel } from "@/components/dashboard/control/SteerForkPanel";
import { SampleRowItem } from "@/components/dashboard/samples/SampleRowItem";

interface Props {
  selected: SelectedCandidate | null;
  onClose: () => void;
}

// Cap rendered rows so a long round can't unbound the inspector's DOM —
// mirrors RoundSamplesBody's PER_GROUP_CAP.
const SAMPLE_CAP = 250;

// The candidate drill-in. Its PRIMARY element is the one runnable-spec surface
// (NodeSurface, values mode, read-only) for the selected searchpoint — the same
// box the chat hero and the steer panel render, so a candidate's spec is never a
// bespoke presentation. The scalar stats + per-sample rows below describe how
// that spec SCORED; Steer & fork opens its editable twin.
export function ScoringInspector({ selected, onClose }: Props) {
  const { dash } = useDashboard();
  const cv = useConnector();
  // Round files follow the VIEWED leaf hop — so an L4 inner loop's candidate
  // resolves its `round_NNNN.json` from the inner cycle's dir, not the outer
  // root's empty `rounds/` (the old "round file not on disk" degradation).
  const { viewedPath } = useWorkspace();
  // Composite + accuracy are deep-audit fields. For a *completed* round they live
  // in `round_NNNN.json::scoreboard`; for the *in-flight* round they live in
  // `dashboard.json`'s live l1_score stats. `useRoundSource` picks one source
  // per the no-stitch rule and never fetches the live round's not-yet-written
  // file (the old 404 + degraded-panel bug).
  const { isLive, doc } = useRoundSource(viewedPath, selected?.round ?? null, dash);
  const [steerOpen, setSteerOpen] = useState(false);

  const data = useMemo<{ composite?: number; accuracy?: number; total?: number } | null>(() => {
    if (!selected) return null;
    if (isLive) {
      const c = liveCandidateRow(dash, selected.round, selected.candidate_id);
      if (!c) return null;
      return {
        composite: c.composite_fitness ?? undefined,
        accuracy: c.accuracy ?? undefined,
        total: c.scored_samples,
      };
    }
    const e = doc?.scoreboard.find((c) => c.candidate_id === selected.candidate_id);
    return e ? { composite: e.composite_fitness, accuracy: e.accuracy, total: e.total } : null;
  }, [selected, isLive, dash, doc]);

  // The per-sample stream for *just this* searchpoint — the same rows the
  // round-wide Samples table shows, sliced to the selected candidate. The
  // CandidateRow carries the `source` tag, so `samplesForRow` routes live vs
  // historical the same way RoundSamplesView does (no merge, no second reader).
  const { byRound } = useRoundCandidates();
  // Arms in this candidate's round — the same rows the sample list slices below.
  const arms = selected ? (byRound.get(selected.round) ?? []).length : 0;
  const row = useMemo<ElectedRow | undefined>(() => {
    if (!selected) return undefined;
    return (byRound.get(selected.round) ?? []).find(
      (c) => c.candidate_id === selected.candidate_id,
    );
  }, [selected, byRound]);
  const samples = useMemo(() => {
    if (!row) return [];
    return samplesForRow(row, dash, doc);
  }, [row, dash, doc]);

  // The selected candidate's runnable spec, through the one observe join every
  // spec surface reads: in-flight round from dashboard.json's live l1_score
  // input, completed round from its round file (no stitch). Round 0 = origin.
  const cfg = !selected
    ? null
    : isLive
      ? liveCandidateObserveConfig(dash, selected.candidate_id, selected.label)
      : candidateObserveConfig(doc, selected.label, selected.label);

  if (!selected) return null;

  return (
    <section className="scoring-inspector" aria-label="Scoring inspector">
      <div className="inspector-head">
        <span>Scoring · {selected.label}</span>
        <button
          type="button"
          className="inspector-close"
          onClick={onClose}
          aria-label="Close inspector"
          title="Close"
        >
          ×
        </button>
      </div>
      {cfg ? (
        <NodeSurface
          node={null}
          point={{ origin_prompt_fields: cfg.promptFields, pipeline_overlay: {} }}
          configSeed={cfg.config}
          schema={cv.nodeConfigSchema}
          outputSchema={cv.nodeOutputSchema}
          label={cfg.label}
          mode="values"
        />
      ) : (
        <p className="inspector-note">
          Searchpoint loading — the spec appears when its round data lands.
        </p>
      )}
      <div className="inspector-body">
        <div className="inspector-row">
          <span className="inspector-key">label</span>
          <span className="inspector-val">{selected.label}</span>
        </div>
        <div className="inspector-row">
          <span className="inspector-key">accuracy</span>
          <span className="inspector-val">{fmtPct1(selected.accuracy)}</span>
        </div>
        {typeof row?.matchedParentAccuracy === "number" && (
          <div className="inspector-row">
            <span className="inspector-key">vs parent</span>
            <span
              className="inspector-val"
              title="The candidate's PARENT — the origin at round 0, the prior round's winner after — re-scored on the samples THIS candidate measured, and the floor the promotion gate compared it against. Under elimination a candidate may run only part of the round's samples, so the parent's full-set rate is the wrong comparison and would read as a phantom lift."
            >
              {fmtPct1(row.matchedParentAccuracy)}
            </span>
          </div>
        )}
        {/* The SERVED lift and its interval, not a difference of two accuracies computed here.
            That subtraction was a number made in the browser (`webapp/CLAUDE.md` § Scoring
            authority) and it carried no uncertainty — so a margin the round could not resolve
            rendered identically to one it could, which is the whole question this row answers. */}
        {typeof row?.matchedParentLift === "number" &&
          typeof row.matchedParentLiftCiLo === "number" &&
          typeof row.matchedParentLiftCiHi === "number" && (
            <div className="inspector-row">
              <span className="inspector-key">lift vs parent</span>
              <span
                className="inspector-val"
                title="Mean per-cell (candidate − parent) across the cells both measured, Student-t bracketed. Pairing removes the parent's cell-to-cell variation, so this is sharper than the candidate's own mean band."
              >
                {fmtSigned(row.matchedParentLift)} [{fmtSigned(row.matchedParentLiftCiLo)},{" "}
                {fmtSigned(row.matchedParentLiftCiHi)}]
                {row.matchedParentLiftCiLo <= 0 && row.matchedParentLiftCiHi >= 0 ? (
                  <span className="l4-eff-flat"> spans 0 — not separable</span>
                ) : (
                  " clears 0"
                )}
              </span>
            </div>
          )}
        {typeof row?.theta === "number" && (
          <div className="inspector-row">
            <span className="inspector-key">ability θ</span>
            <span
              className="inspector-val"
              title="Difficulty-adjusted Rasch ability — the metric the round winner is elected on. Clearing harder samples is worth more than more wins on easy ones, so a higher θ can beat a higher accuracy."
            >
              {row.theta.toFixed(2)}
              {typeof row.theta_se === "number" ? ` ± ${row.theta_se.toFixed(2)}` : ""}
            </span>
          </div>
        )}
        {data && typeof data.composite === "number" && (
          <div className="inspector-row">
            <span className="inspector-key">composite</span>
            <span className="inspector-val">{data.composite.toFixed(4)}</span>
          </div>
        )}
        {data && typeof data.accuracy === "number" && typeof data.total === "number" && (
          <div className="inspector-row">
            <span className="inspector-key">accuracy</span>
            <span className="inspector-val">
              {(data.accuracy * 100).toFixed(1)}% of {data.total}
            </span>
          </div>
        )}
        <div className="inspector-row">
          <span className="inspector-key">winner</span>
          {/* A crown over no rivals is not an election (`derivations/election.ts`):
              round 0 runs one arm. `arms === 0` means the rows have not loaded, so
              it reports the served fact rather than guessing. */}
          <span className="inspector-val">
            {!selected.is_winner ? "no" : arms === 1 ? "yes — uncontested" : "yes"}
          </span>
        </div>
        {data == null && (
          <div className="inspector-note">
            {isLive
              ? `Scoring in progress for R${selected.round} — composite + accuracy appear as this candidate's samples land.`
              : `Round file not yet on disk for R${selected.round} — showing only the in-tree summary.`}
          </div>
        )}
      </div>
      {samples.length > 0 && (
        <div className="inspector-samples">
          <div className="rsv-group-head" aria-hidden>
            <span className="rsv-cand-label">{selected.label} · samples</span>
            {/* Served numbers, not a tally over the rendered rows: the two disagreed
                whenever this list was capped or still filling. R-36 — the backend
                computes, the webapp never recomputes. The HIT/MISS pair is gone with
                the served `hits`; on a graded scorer it read "HIT 0 / MISS n" beside a
                real accuracy, and on a binary one it said the same thing twice. */}
            {data && typeof data.accuracy === "number" && typeof data.total === "number" && (
              <span className="rsv-tally">
                {(data.accuracy * 100).toFixed(0)}% of {data.total}
              </span>
            )}
          </div>
          <div className="rsv-rows">
            {samples.slice(0, SAMPLE_CAP).map((s) => (
              <SampleRowItem key={s.key} row={s} />
            ))}
            {samples.length > SAMPLE_CAP && (
              <div className="rsv-empty-row">
                +{samples.length - SAMPLE_CAP} more (rendering capped at {SAMPLE_CAP}).
              </div>
            )}
          </div>
        </div>
      )}
      <div className="inspector-actions">
        <button
          type="button"
          className="fork-button"
          onClick={() => setSteerOpen(true)}
          title="Open this searchpoint in the control panel — review or edit its evolved prompt, node config, and run limits, then fork-continue optimizing from it. Edits are optional."
        >
          Steer &amp; fork
        </button>
      </div>
      {/* Steering is its own act with its own home — a modal control panel that
          opens straight from the inspector (no tab hop). The fork continues
          from this searchpoint (always `operator_steered`); edits are optional. */}
      {steerOpen && (
        <Dialog
          open
          title={`Steer & fork · ${selected.label}`}
          onClose={() => setSteerOpen(false)}
        >
          <SteerForkPanel
            candidate={selected}
            onDone={() => setSteerOpen(false)}
            onCancel={() => setSteerOpen(false)}
          />
        </Dialog>
      )}
    </section>
  );
}

"use client";
import { useMemo, useState } from "react";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { fmtPct1 } from "@/lib/format";
import type { CandidateRow, ScoreboardEntry, SelectedCandidate } from "@/lib/types";
import { liveCandidate } from "@/lib/poll";
import { samplesForRow } from "@/lib/derivations";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { Dialog } from "@/components/ui";
import { SteerForkPanel } from "@/components/dashboard/control/SteerForkPanel";
import { SampleRowItem } from "@/components/dashboard/samples/SampleRowItem";

interface Props {
  selected: SelectedCandidate | null;
  onClose: () => void;
}

// Cap rendered rows so a long round can't unbound the inspector's DOM —
// mirrors RoundSamplesBody's PER_GROUP_CAP.
const SAMPLE_CAP = 250;

export function ScoringInspector({ selected, onClose }: Props) {
  const { dash } = useDashboard();
  const { campaignId, cycleId } = useWorkspace();
  // Composite + hits are deep-audit fields. For a *completed* round they live
  // in `round_NNNN.json::scoreboard`; for the *in-flight* round they live in
  // `dashboard.json`'s live l1_score stats. `useRoundSource` picks one source
  // per the no-stitch rule and never fetches the live round's not-yet-written
  // file (the old 404 + degraded-panel bug).
  const { isLive, doc } = useRoundSource(
    campaignId,
    cycleId,
    selected?.round ?? null,
    dash,
  );
  const [steerOpen, setSteerOpen] = useState(false);

  const data = useMemo<{ composite?: number; hits?: number; total?: number } | null>(() => {
    if (!selected) return null;
    if (isLive) {
      const c = liveCandidate(dash, selected.round, selected.candidate_id);
      if (!c?.stats) return null;
      return { composite: c.stats.composite_fitness, hits: c.stats.hits, total: c.stats.total };
    }
    const scoreboard = doc?.scoreboard as ScoreboardEntry[] | undefined;
    const e = scoreboard?.find((c) => (c.candidate_id ?? "") === selected.candidate_id);
    return e ? { composite: e.composite, hits: e.hits, total: e.total } : null;
  }, [selected, isLive, dash, doc]);

  // The per-sample stream for *just this* searchpoint — the same rows the
  // round-wide Samples table shows, sliced to the selected candidate. The
  // CandidateRow carries the `source` tag, so `samplesForRow` routes live vs
  // historical the same way RoundSamplesView does (no merge, no second reader).
  const { byRound } = useRoundCandidates();
  const samples = useMemo(() => {
    if (!selected) return [];
    const row: CandidateRow | undefined = (byRound.get(selected.round) ?? []).find(
      (c) => c.candidate_id === selected.candidate_id,
    );
    if (!row) return [];
    return samplesForRow(row, dash, doc);
  }, [selected, byRound, dash, doc]);

  const tally = useMemo(() => {
    const hits = samples.reduce((n, s) => n + (s.status === "HIT" ? 1 : 0), 0);
    return { hits, misses: samples.length - hits };
  }, [samples]);

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
      <div className="inspector-body">
        <div className="inspector-row">
          <span className="inspector-key">label</span>
          <span className="inspector-val">{selected.label}</span>
        </div>
        <div className="inspector-row">
          <span className="inspector-key">accuracy</span>
          <span className="inspector-val">{fmtPct1(selected.accuracy)}</span>
        </div>
        {data && typeof data.composite === "number" && (
          <div className="inspector-row">
            <span className="inspector-key">composite</span>
            <span className="inspector-val">{data.composite.toFixed(4)}</span>
          </div>
        )}
        {data && typeof data.hits === "number" && typeof data.total === "number" && (
          <div className="inspector-row">
            <span className="inspector-key">hits</span>
            <span className="inspector-val">{data.hits} / {data.total}</span>
          </div>
        )}
        <div className="inspector-row">
          <span className="inspector-key">winner</span>
          <span className="inspector-val">{selected.is_winner ? "yes" : "no"}</span>
        </div>
        {data == null && (
          <div className="inspector-note">
            {isLive
              ? `Scoring in progress for R${selected.round} — composite + hits appear as this candidate's samples land.`
              : `Round file not yet on disk for R${selected.round} — showing only the in-tree summary.`}
          </div>
        )}
      </div>
      {samples.length > 0 && (
        <div className="inspector-samples">
          <div className="rsv-group-head" aria-hidden>
            <span className="rsv-cand-label">{selected.label} · samples</span>
            <span className="rsv-tally">
              <span className="tag-hit">HIT {tally.hits}</span>
              <span className="tag-miss">MISS {tally.misses}</span>
            </span>
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

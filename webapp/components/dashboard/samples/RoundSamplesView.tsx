"use client";
import { useMemo, useState } from "react";
import { CardFrame } from "@/components/ui";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useEffectiveRound } from "@/lib/hooks/useEffectiveRound";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import {
  roundCandidatesByRound,
  historicalSamplesFor,
  liveSamplesFor,
} from "@/lib/derivations";
import type { CandidateRow, SampleRow } from "@/lib/types";
import { RoundSamplesBody, type StatusFilter } from "./RoundSamplesBody";
import { RoundSamplesEmptyState } from "./RoundSamplesEmptyState";

// Single per-round samples surface. Live mode reads from
// `dashboard.json` only; historical mode reads `round_NNNN.json`
// only — the two paths never merge (CLAUDE.md no-stitch rule).
// The candidate list comes from `roundCandidatesByRound` in both
// modes so the displayed groups stay aligned with lineage + fitness.

export function RoundSamplesView() {
  const { dash, status } = useDashboard();
  const { campaignId, cycleId } = useWorkspace();
  const { setSelectionForCandidate, setSelectionForRound, candidate } = useSelection();
  // The active round — the explicit pick, else the live in-flight round —
  // from the single resolver every round-scoped surface shares. `useRoundSource`
  // owns the live/historical guard + the historical round-file fetch.
  const { round: effectiveRound } = useEffectiveRound();
  const {
    isLive: isLiveView,
    doc: roundDoc,
    loading: roundLoading,
    error: roundError,
  } = useRoundSource(campaignId, cycleId, effectiveRound, dash);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [candFilter, setCandFilter] = useState<string>("all");

  // Candidate list for this round — single source of truth shared with
  // LineageTree and FitnessPanel. Round 0 is the origin (one candidate, "C0")
  // and shows its per-sample stream from round_0000.json like any round.
  const byRound = useMemo(() => roundCandidatesByRound(dash), [dash]);
  const candidates: CandidateRow[] = useMemo(() => {
    if (effectiveRound == null) return [];
    return byRound.get(effectiveRound) ?? [];
  }, [byRound, effectiveRound]);

  // Build per-candidate samples lists. Live mode pulls directly from
  // the in-flight projection (compact string lines parsed once via
  // `parseSampleLine`); historical mode pulls from the round file's
  // `all_candidate_results`. Both readers return the same `SampleRow`
  // shape so the renderer below stays source-agnostic.
  const groups = useMemo(() => {
    if (effectiveRound == null) return [];
    const out: { candidate: CandidateRow; samples: SampleRow[] }[] = [];
    for (const c of candidates) {
      const raw = isLiveView
        ? liveSamplesFor(dash, effectiveRound, c.candidate_id)
        : historicalSamplesFor(roundDoc, effectiveRound, c.candidate_id);
      const filtered = raw.filter((s) => {
        if (statusFilter === "all") return true;
        if (statusFilter === "hit") return s.status === "HIT";
        return s.status === "MISS";
      });
      out.push({ candidate: c, samples: filtered });
    }
    if (candFilter !== "all") {
      return out.filter((g) => g.candidate.candidate_id === candFilter);
    }
    return out;
  }, [candidates, candFilter, statusFilter, isLiveView, dash, roundDoc, effectiveRound]);

  const totalRows = useMemo(
    () => groups.reduce((n, g) => n + g.samples.length, 0),
    [groups],
  );

  const title =
    effectiveRound == null
      ? "Samples"
      : isLiveView
        ? `Samples · R${effectiveRound}`
        : `Round ${effectiveRound} · samples`;

  const liveness = status === "live" ? "rolling" : "snapshot";
  const actions =
    effectiveRound == null ? null : isLiveView ? (
      <span className="badge">{liveness}</span>
    ) : (
      <button
        type="button"
        className="badge round-back-live"
        onClick={() => setSelectionForRound(null)}
        title="Follow the in-flight round again"
      >
        ← live
      </button>
    );

  return (
    <CardFrame
      className="round-samples-view"
      headingTag="h2"
      title={title}
      actions={actions}
    >
      {effectiveRound == null ? (
        <div className="samples-empty">
          No rounds yet. Samples appear here once the optimizer starts
          scoring — start it with{" "}
          <code>python -m promptpotter resume</code>.
        </div>
      ) : !isLiveView && roundLoading ? (
        <div className="samples-empty">Loading round {effectiveRound}…</div>
      ) : !isLiveView && roundError ? (
        <div className="samples-empty">
          Could not load round {effectiveRound}: {roundError}
        </div>
      ) : candidates.length === 0 ? (
        <RoundSamplesEmptyState status={status} isLiveView={isLiveView} round={effectiveRound} />
      ) : (
        <RoundSamplesBody
          candidates={candidates}
          groups={groups}
          totalRows={totalRows}
          statusFilter={statusFilter}
          onStatusFilter={setStatusFilter}
          candFilter={candFilter}
          onCandFilter={setCandFilter}
          selectedCandidate={candidate}
          onSelectCandidate={setSelectionForCandidate}
        />
      )}
    </CardFrame>
  );
}

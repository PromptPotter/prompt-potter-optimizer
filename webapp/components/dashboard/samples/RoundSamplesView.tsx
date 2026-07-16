"use client";
import { useMemo, useState } from "react";
import { CardFrame } from "@/components/ui";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useEffectiveRound } from "@/lib/hooks/useEffectiveRound";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { useForest } from "@/lib/hooks/useForest";
import { innerPanelIndex, samplesForRow } from "@/lib/derivations";
import type { CycleListEntry } from "@/lib/api";
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
  // Round files follow the VIEWED leaf hop (an L4 inner loop reads the inner
  // cycle's `rounds/`, not the outer root's) — the same address the dashboard
  // stream uses. `useRoundSource` owns the descend-aware fetch + live guard.
  // `leafIsL4`: are this course's samples inner campaigns rather than scored rows?
  // The workspace's answer, off the LEAF's campaign — the same one the candidates
  // card branches on, so the two surfaces cannot disagree about the course.
  const { viewedPath, leafCycleId, leafIsL4: isL4, drillInto } = useWorkspace();
  // The sandbox this course's cells ran in. Fetched only when it has one — and
  // shared with every other surface reading this same path, one poll between them.
  const inner = useForest(viewedPath ?? [], isL4 && viewedPath != null);
  const panel = useMemo(() => innerPanelIndex(inner.cycles), [inner.cycles]);
  const openRun = (run: CycleListEntry): void => drillInto(run.campaign_id, run.cycle_id);
  const { setSelectionForCandidate, setSelectionForRound, candidate } = useSelection();
  // The groups are read from the leaf's round source, so the leaf is the cycle
  // that produced these candidates — the selection names it, and the panes it
  // scopes read that same hop.
  const onSelectCandidate = (c: CandidateRow | null): void =>
    setSelectionForCandidate(
      c && leafCycleId
        ? {
            cycle_id: leafCycleId,
            round: c.round,
            candidate_id: c.candidate_id,
            label: c.label,
            accuracy: c.accuracy,
            is_winner: c.is_winner,
          }
        : null,
    );
  // The active round — the explicit pick, else the live in-flight round —
  // from the single resolver every round-scoped surface shares.
  const { round: effectiveRound } = useEffectiveRound();
  const {
    isLive: isLiveView,
    doc: roundDoc,
    loading: roundLoading,
    error: roundError,
  } = useRoundSource(viewedPath, effectiveRound, dash);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [candFilter, setCandFilter] = useState<string>("all");

  // Candidate list for this round — single source of truth shared with
  // the candidates card via the spine hook. Round 0 is the origin (one
  // candidate, "C0") and shows its per-sample stream from round_0000.json like
  // any round.
  const { byRound } = useRoundCandidates();
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
      // `samplesForRow` selects live vs historical off the row's own `source`
      // tag (the spine sets it) — same routing the candidates card's bars use, never a
      // merge. `roundDoc` is null on the live round (the fetch is idled), and
      // an in-flight row reads `dash`, so the source is unambiguous.
      const raw = samplesForRow(c, dash, roundDoc);
      // An L4 cell has no HIT/MISS to filter on — it was optimized, not scored, so
      // every `status` is null. The control is hidden in that mode; skipping the
      // filter here keeps a stale "hit" pick from blanking the panel.
      const filtered = isL4
        ? raw
        : raw.filter((s) => {
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
  }, [candidates, candFilter, statusFilter, dash, roundDoc, effectiveRound, isL4]);

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
          cycleId={leafCycleId}
          onSelectCandidate={onSelectCandidate}
          panel={isL4 ? panel : null}
          onOpenRun={openRun}
        />
      )}
    </CardFrame>
  );
}

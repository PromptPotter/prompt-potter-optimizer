"use client";
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { fetchCampaignLineage, postCleanupEmpty } from "@/lib/api";
import type { CampaignLineageCycle } from "@/lib/api";
import type { DashboardSnapshot } from "@/lib/poll";
import { candidateLabel } from "@/lib/candidate-label";
import { rootCycleId, sessionIndexOf, shortFamilyTail } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { bumpRevalidation, useRevalidation } from "@/lib/revalidate";
import { CardFrame } from "@/components/ui/Card";
import { useFetch } from "@/lib/hooks/useFetch";
import { useExpandedDashboards } from "@/lib/hooks/useExpandedDashboards";
import { useStableContent } from "@/lib/stable";
import { roundCandidatesByRound } from "@/lib/derivations/round-candidates";
import {
  buildTree,
  countDescendants,
  type CycleDetail,
  type CycleNode,
  type DetailByCycle,
} from "./layout";
import { Forest } from "./Forest";
import { CleanupConfirmModal } from "./CleanupConfirmModal";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

interface Props {
  // Live dashboard for the IN-VIEW cycle. Used to override that one cycle's
  // expanded candidate detail with live (2 s, in-flight) data — every other
  // cycle reads the fetched lineage snapshot. Source-by-cycle-role, never a
  // per-field merge of the two (the banned stitch).
  dash: DashboardSnapshot | null;
  // The campaign whose lineage to render. A campaign is a FOREST: it holds
  // N session roots, each with its own fork tree. One fetch returns every
  // cycle; we render one cladogram per session.
  campaignId: string | null;
  // The cycle currently in view — its lane is the one that expands into the
  // intra-cycle candidate cladogram (every other lane stays compact), and it's
  // the lane that gets the live `dash` override.
  cycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}

// Lineage snapshot → normalized detail. Candidates already arrive sorted by
// rank; the display index drives the short "C{r}.{n}" label so it matches the
// live (dashboard.json) labels for the active cycle.
function detailFromLineage(c: CampaignLineageCycle): CycleDetail {
  return {
    rounds: c.rounds.map((r) => ({
      round: r.round,
      candidates: r.candidates.map((cand, i) => ({
        candidateId: cand.candidate_id || `r${r.round}_${i}`,
        label: candidateLabel(r.round, i),
        accuracy: cand.accuracy,
        isWinner: cand.is_winner,
      })),
    })),
    originAccuracy: c.origin_accuracy,
  };
}

// dashboard.json → normalized detail for the in-view cycle. Rides the same
// per-round candidate derivation every other live surface uses.
function detailFromDash(dash: DashboardSnapshot): CycleDetail {
  const byRound = roundCandidatesByRound(dash);
  const rounds = [...byRound.keys()]
    .filter((r) => r > 0)
    .sort((a, b) => a - b)
    .map((round) => ({
      round,
      candidates: (byRound.get(round) ?? []).map((row) => ({
        candidateId: row.candidate_id,
        label: row.label,
        accuracy: row.accuracy,
        isWinner: row.is_winner,
      })),
    }));
  const origin = byRound.get(0)?.[0];
  return { rounds, originAccuracy: origin?.accuracy ?? null };
}

export const FamilyTree = memo(function FamilyTree({
  dash,
  campaignId,
  cycleId,
  onSelectCycle,
}: Props) {
  const [tick, setTick] = useState(0);
  // Refetch the campaign-wide tree the instant any mutation resolves (fork,
  // cleanup, lifecycle) — the same revalidation seam the poll loops ride. A
  // fresh fork mints its index.json before `postForkCycle` returns + bumps
  // revalidation, so the new lane lands here without waiting for a window
  // refocus. `cycleId` is deliberately NOT a dep: /lineage is campaign-scoped,
  // so a same-campaign cycle switch returns identical data — keying on it would
  // only blank-flash the card for no gain.
  const reval = useRevalidation();
  const { data } = useFetch(
    campaignId ? (s) => fetchCampaignLineage(campaignId, s) : null,
    [campaignId, tick, reval],
  );

  // Independent per-cycle expand state — one unified tree where every cycle
  // (origin/root included) opens its intra-cycle candidate cladogram in place,
  // any number at once. Ephemeral (no persistence). Starting view: the in-view
  // cycle expanded, everything else compact (per operator spec). A campaign
  // switch resets to that clean view; selecting another fork ensure-expands it
  // without collapsing the others.
  const [expanded, setExpanded] = useState<Set<string>>(() =>
    cycleId ? new Set([cycleId]) : new Set(),
  );
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  const [prevCycle, setPrevCycle] = useState(cycleId);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setPrevCycle(cycleId);
    setExpanded(cycleId ? new Set([cycleId]) : new Set());
  } else if (cycleId !== prevCycle) {
    setPrevCycle(cycleId);
    if (cycleId) {
      setExpanded((prev) => (prev.has(cycleId) ? prev : new Set(prev).add(cycleId)));
    }
  }

  // In-place expand/collapse toggle for one cycle's lane. Pure view state —
  // never changes the dashboard's selected cycle (that's the sidebar's job and
  // a candidate click's job).
  const onLaneActivate = useCallback((cid: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid);
      else next.add(cid);
      return next;
    });
  }, []);

  // The campaign's session roots — one cladogram per session.
  const rootCycleIds = useMemo(() => {
    const roots = (data?.cycles ?? [])
      .filter((c) => c.sibling_kind === "root")
      .map((c) => c.cycle_id);
    roots.sort((a, b) => sessionIndexOf(a) - sessionIndexOf(b));
    return roots;
  }, [data]);

  // Other EXPANDED lanes (not the selected one) follow their OWN live
  // dashboard.json — the web-only multi-cycle view. Minimal: polls only these
  // ids, 304s when idle. The selected cycle already rides the `dash` prop.
  const expandedOthers = useMemo(
    () => [...expanded].filter((cid) => cid !== cycleId),
    [expanded, cycleId],
  );
  const liveDashboards = useExpandedDashboards(campaignId, expandedOthers);

  // Normalized per-cycle detail for the whole campaign. Base = lineage snapshot
  // (round-file candidates); each expanded lane is overlaid with its live
  // dashboard when that snapshot actually carries data (so a warming/empty poll
  // never wipes the snapshot's candidates). Source-by-cycle, not a per-field
  // merge. Stabilized by content so Forest's layout memo only recomputes on a
  // real shape change.
  const detailEntries = useStableContent(
    useMemo(() => {
      const map = new Map<string, CycleDetail>();
      for (const c of data?.cycles ?? []) map.set(c.cycle_id, detailFromLineage(c));
      const overlay = (cid: string | null, snap: DashboardSnapshot | null): void => {
        if (!cid || !snap) return;
        const live = detailFromDash(snap);
        if (live.rounds.length > 0 || live.originAccuracy != null) map.set(cid, live);
        else if (!map.has(cid)) map.set(cid, live);
      };
      overlay(cycleId, dash); // selected cycle (live `dash` prop)
      for (const [cid, snap] of liveDashboards) overlay(cid, snap); // expanded others
      return [...map.entries()];
    }, [data, cycleId, dash, liveDashboards]),
  );
  const detailByCycle: DetailByCycle = useMemo(
    () => new Map(detailEntries),
    [detailEntries],
  );

  // Single batch-cleanup modal — campaign-wide. Empty-row stubs accumulate
  // because the fork-creation paths mint the cycle dir BEFORE the first
  // round runs; an interrupt between dir-mint and first-round leaves a stub.
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [cleaning, setCleaning] = useState(false);
  const [cleanupAcked, setCleanupAcked] = useState(false);

  const confirmCleanup = useCallback(async () => {
    if (!campaignId || rootCycleIds.length === 0) return;
    setCleaning(true);
    setCleanupError(null);
    try {
      await postCleanupEmpty(campaignId, rootCycleIds[0]);
      setCleanupAcked(true);
      setCleanupOpen(false);
      setTick((t) => t + 1);
      bumpRevalidation();
    } catch (err) {
      setCleanupError((err as Error).message);
    } finally {
      setCleaning(false);
    }
  }, [campaignId, rootCycleIds]);

  // Window refocus ⇒ re-fetch the lineage, so forks/cleanups made from
  // another tab or the CLI surface without a manual reload.
  useEffect(() => {
    const onFocus = () => setTick((t) => t + 1);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  const stubCount = useMemo(
    () =>
      (data?.cycles ?? []).filter(
        (c) => c.rounds.length === 0 && c.sibling_kind !== "root",
      ).length,
    [data],
  );

  // One cladogram per session — every session root renders, including a lone
  // root with no forks (its single lane carries the intra-cycle view).
  const forests = useMemo(() => {
    if (!data) return [];
    return rootCycleIds
      .map((rootId) => {
        const tree = buildTree(rootId, data.cycles);
        return tree ? { rootId, tree } : null;
      })
      .filter((f): f is { rootId: string; tree: CycleNode } => f !== null);
  }, [data, rootCycleIds]);

  const totalDesc = forests.reduce((n, f) => n + countDescendants(f.tree), 0);
  const multiSession = rootCycleIds.length > 1;

  // Empty state for the in-view cycle (no rounds yet) — distinguishes an
  // inherited fork from a fresh cycle waiting for round 1.
  const viewedDetail = cycleId ? detailByCycle.get(cycleId) : undefined;
  const viewedHasRounds = (viewedDetail?.rounds.length ?? 0) > 0;
  const parentId = cycleId ? rootCycleId(cycleId) : null;
  const isInheritedSibling = parentId != null && parentId !== cycleId;

  return (
    <CardFrame
      className="lineage-card"
      title={<span>Lineage</span>}
      actions={
        <span className="family-cladogram-head-meta">
          <span className="badge">
            {totalDesc} {totalDesc === 1 ? "descendant" : "descendants"}
          </span>
          {stubCount > 0 && (
            <button
              type="button"
              className="family-cladogram-cleanup-btn"
              onClick={() => {
                setCleanupError(null);
                setCleanupOpen(true);
              }}
              title="Delete every empty-stub fork in this campaign from disk"
            >
              Clean up {stubCount} stub{stubCount === 1 ? "" : "s"}
            </button>
          )}
          {cleanupAcked && stubCount === 0 && (
            <span className="family-cladogram-cleanup-done" title="Last cleanup result">
              cleaned
            </span>
          )}
        </span>
      }
    >
      <RotatePrompt surfaceName="The lineage tree" skipRender>
        <section className="family-cladogram" aria-label="Campaign lineage tree">
          {forests.map((f) => (
            <Forest
              key={f.rootId}
              tree={f.tree}
              campaignId={campaignId ?? ""}
              cycleId={cycleId}
              detailByCycle={detailByCycle}
              expanded={expanded}
              onLaneActivate={onLaneActivate}
              onSelectCycle={onSelectCycle}
              sessionLabel={
                multiSession ? `Session ${sessionIndexOf(f.rootId)}` : null
              }
            />
          ))}
          {!viewedHasRounds && (
            <div className="lineage-empty">
              {isInheritedSibling && parentId ? (
                <>
                  inherited from{" "}
                  {campaignId ? (
                    <button
                      type="button"
                      className="lineage-inherit-link"
                      onClick={() => onSelectCycle(campaignId, parentId)}
                      title={`Switch to ${parentId}`}
                    >
                      {shortFamilyTail(parentId) || parentId}
                    </button>
                  ) : (
                    <span>{shortFamilyTail(parentId) || parentId}</span>
                  )}
                  {dash?.best != null ? ` · best ${fmtPct0(dash.best)}` : ""}
                  {" · no new rounds yet"}
                </>
              ) : viewedDetail?.originAccuracy != null ? (
                `origin ${fmtPct0(viewedDetail.originAccuracy)} · waiting for round 1`
              ) : (
                "No rounds on disk yet — the tree appears once round 1 lands."
              )}
            </div>
          )}
          {cleanupOpen && (
            <CleanupConfirmModal
              stubCount={stubCount}
              cleaning={cleaning}
              error={cleanupError}
              onCancel={() => {
                setCleanupOpen(false);
                setCleanupError(null);
              }}
              onConfirm={confirmCleanup}
            />
          )}
        </section>
      </RotatePrompt>
    </CardFrame>
  );
});

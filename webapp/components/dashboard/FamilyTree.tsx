"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchCampaignLineage,
  postCleanupEmpty,
  type CampaignLineageResponse,
} from "@/lib/api";
import { sessionIndexOf } from "@/lib/ids";
import { bumpRevalidation } from "@/lib/revalidate";
import { buildTree, countDescendants, type CycleNode } from "./family-tree/layout";
import { Forest } from "./family-tree/Forest";
import { CleanupConfirmModal } from "./family-tree/CleanupConfirmModal";

interface Props {
  // The campaign whose lineage to render. A campaign is a FOREST: it holds
  // N session roots, each with its own fork tree. One fetch returns every
  // cycle; we render one cladogram per session.
  campaignId: string | null;
  // The cycle currently in view — drives the selected-lane highlight.
  cycleId: string | null;
  // A unit is the pair (campaignId, cycleId); this tree is scoped to one
  // campaign, so it passes its own campaignId alongside each cycle_id.
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}

export function FamilyTree({ campaignId, cycleId, onSelectCycle }: Props) {
  const [data, setData] = useState<CampaignLineageResponse | null>(null);
  const [tick, setTick] = useState(0);
  // The campaign's session roots — one cladogram per session. A campaign
  // is a forest: every `sibling_kind === "root"` cycle is a session.
  const rootCycleIds = useMemo(() => {
    const roots = (data?.cycles ?? [])
      .filter((c) => c.sibling_kind === "root")
      .map((c) => c.cycle_id);
    roots.sort((a, b) => sessionIndexOf(a) - sessionIndexOf(b));
    return roots;
  }, [data]);

  // Single batch-cleanup modal — campaign-wide. Empty-row stubs accumulate
  // because the fork-creation paths mint the cycle dir BEFORE the first
  // round runs; an interrupt between dir-mint and first-round leaves a
  // stub forever.
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [cleaning, setCleaning] = useState(false);
  const [lastCleanupCount, setLastCleanupCount] = useState<number | null>(null);

  const confirmCleanup = useCallback(async () => {
    if (!campaignId || rootCycleIds.length === 0) return;
    setCleaning(true);
    setCleanupError(null);
    try {
      const r = await postCleanupEmpty(campaignId, rootCycleIds[0]);
      setLastCleanupCount(r.deleted_cycle_ids.length);
      setCleanupOpen(false);
      setTick((t) => t + 1);
      // Re-tick the workspace poll so the sidebar drops the deleted cycles
      // at once, not on its next interval.
      bumpRevalidation();
    } catch (err) {
      setCleanupError((err as Error).message);
    } finally {
      setCleaning(false);
    }
  }, [campaignId, rootCycleIds]);

  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        const r = await fetchCampaignLineage(campaignId, ac.signal);
        if (!cancelled) setData(r);
      } catch {
        // Silent — sidebar covers navigation. Empty render below.
      }
    })();
    const onFocus = () => setTick((t) => t + 1);
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      ac.abort();
      window.removeEventListener("focus", onFocus);
    };
  }, [campaignId, tick]);

  // Campaign-wide stub count — every empty non-root cycle, across every
  // session. Same definition the server-side cleanup guard uses.
  const stubCount = useMemo(
    () =>
      (data?.cycles ?? []).filter(
        (c) => c.rounds.length === 0 && c.sibling_kind !== "root",
      ).length,
    [data],
  );

  // Per-session cladograms — only sessions that actually branched render a
  // tree (a lone root with no forks has nothing to draw).
  const forests = useMemo(() => {
    if (!data) return [];
    return rootCycleIds
      .map((rootId) => {
        const tree = buildTree(rootId, data.cycles);
        if (!tree || tree.children.length === 0) return null;
        return { rootId, tree };
      })
      .filter((f): f is { rootId: string; tree: CycleNode } => f !== null);
  }, [data, rootCycleIds]);

  if (!campaignId || forests.length === 0) return null;
  const totalDesc = forests.reduce(
    (n, f) => n + countDescendants(f.tree),
    0,
  );
  const multiSession = rootCycleIds.length > 1;

  return (
    <section className="family-cladogram" aria-label="Campaign lineage tree">
      <div className="family-cladogram-head">
        <span>Campaign lineage</span>
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
          {lastCleanupCount != null && stubCount === 0 && (
            <span className="family-cladogram-cleanup-done" title="Last cleanup result">
              cleaned {lastCleanupCount}
            </span>
          )}
        </span>
      </div>
      {forests.map((f) => (
        <Forest
          key={f.rootId}
          tree={f.tree}
          campaignId={campaignId}
          cycleId={cycleId}
          onSelectCycle={onSelectCycle}
          sessionLabel={
            multiSession ? `Session ${sessionIndexOf(f.rootId)}` : null
          }
        />
      ))}
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
  );
}

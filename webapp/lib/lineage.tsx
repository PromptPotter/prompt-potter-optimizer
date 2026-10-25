"use client";
// A remembered individual id resolves through `answers_for_id`, never the first row holding it.

import { createContext, useContext, useMemo, useState } from "react";
import { subtreeRead, type SubtreeMask, type SubtreeMoment } from "@/lib/api";
import { ABORT_LENS_LABELS, SCORE_LENS_LABELS } from "@/lib/api/types.generated";
import type { HierarchyCampaign, HierarchyCourse } from "@/lib/api/types";
import { phaseIs } from "@/lib/run-phase";
import { courseAt, coursesOf, indexLineage, type LineageIndex } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";
import { lensOf, useScoringMask } from "@/lib/scoring-mask";
import { useScoringMaskSeed } from "@/lib/hooks/useServedCriterion";
import { useDebounced } from "@/lib/hooks/useDebounced";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { useRegistry } from "@/lib/registry";
import { useSelection } from "@/lib/SelectionContext";
import { useWorkspace } from "@/lib/workspace";

const POLL_MS = 5000;

const LENS_LABELS: Record<string, string> = { ...SCORE_LENS_LABELS, ...ABORT_LENS_LABELS };

export interface CampaignTree {
  root: HierarchyCampaign | null;
  loaded: boolean;
  failed: boolean;
}

const EMPTY: CampaignTree = { root: null, loaded: false, failed: false };

export interface ViewedLineage {
  tree: HierarchyCampaign | null;
  index: LineageIndex;
  // "" (off), "score:<formula>", or "abort:<variant>"; the open scoring-mask panel overrides it.
  lens: string;
  setLens: (lens: string) => void;
  maskActive: boolean;
  maskLabel: string;
  scoringMaskActive: boolean;
}

interface TreeAddressing {
  viewedAddress: string | null;
  viewedMask: SubtreeMask | null;
  viewedMoment: SubtreeMoment | null;
  // Campaigns whose every course entry is settled: read once, re-asked only by an invalidation.
  resting: ReadonlySet<string>;
}

// Two contexts: a sidebar row naming another campaign's read must not re-render per viewed-tree landing.
const TreeAddressingContext = createContext<TreeAddressing | null>(null);
const ViewedLineageContext = createContext<ViewedLineage | null>(null);

function useTree(
  address: string | null,
  mask: SubtreeMask | null,
  moment: SubtreeMoment | null,
  resting: ReadonlySet<string>,
): CampaignTree {
  const rests = address !== null && resting.has(address);
  // No `onGone`: the dashboard read owns that verdict, and this reader may be another campaign's row.
  const read = useRead(address ? subtreeRead(address, mask, moment) : null, {
    auth: true,
    intervalMs: rests ? undefined : POLL_MS,
  });
  const served = shownData(read);
  const root = served?.level === "campaign" ? served : null;
  const failed = read.status === "failed" && root === null;
  return useMemo(
    () => (root === null && !failed ? EMPTY : { root, loaded: root !== null, failed }),
    [root, failed],
  );
}

export function LineageProvider({
  campaignId,
  cycleId,
  children,
}: {
  campaignId: string | null;
  cycleId: string | null;
  children: React.ReactNode;
}) {
  const [lens, setLens] = useState<string>("");
  // This provider lives at the shell root, so a lens would otherwise leak across campaigns.
  const [prevCampaign, setPrevCampaign] = useState(campaignId);
  if (campaignId !== prevCampaign) {
    setPrevCampaign(campaignId);
    setLens("");
  }

  // Seeded here: any later, and a cycle's first tree goes out unmasked.
  useScoringMaskSeed();
  const { open: maskOpen, mask } = useScoringMask();
  const { sampleSet } = useSelection();
  const samples = useMemo(
    () => (sampleSet && sampleSet.length > 0 ? sampleSet : null),
    [sampleSet],
  );
  const liveMaskLens = useMemo(() => (maskOpen ? lensOf(mask) : null), [maskOpen, mask]);
  const maskLens = useDebounced(liveMaskLens, 250);
  const lensParam = maskOpen ? maskLens : lens || null;
  const maskLabel = maskOpen ? "Scoring mask" : samples ? "Sample set" : (LENS_LABELS[lens] ?? "");

  // Keyed on the campaign, not `cycleId`: a same-campaign cycle switch keeps one read.
  const viewedAddress = campaignId && cycleId ? campaignId : null;
  const viewedMask = useMemo<SubtreeMask | null>(
    () => (lensParam || samples ? { lens: lensParam, samples } : null),
    [lensParam, samples],
  );

  const { entries } = useRegistry();
  const resting = useMemo(
    () =>
      new Set(
        entries
          .filter(
            (e) =>
              coursesOf(e).length > 0 &&
              coursesOf(e).every((c) => phaseIs(c.state.run_phase, "settled")),
          )
          .map((e) => e.address),
      ),
    [entries],
  );

  // The offset is the viewed LEAF's, which the campaign-keyed subtree is not addressed by.
  const { at, viewedPath: leafPath } = useWorkspace();
  const viewedMoment = useMemo<SubtreeMoment | null>(
    () => (at !== null && leafPath !== null ? { at, cycle: leafPath } : null),
    [at, leafPath],
  );

  const viewedTree = useTree(viewedAddress, viewedMask, viewedMoment, resting).root;
  const index = useMemo(() => indexLineage(viewedTree), [viewedTree]);

  const maskActive = useMemo(
    () => index.candidates.some((c) => c.facts.divergence !== null || c.facts.divergent),
    [index],
  );

  const viewed = useMemo<ViewedLineage>(
    () => ({
      tree: viewedTree,
      index,
      lens,
      setLens,
      maskActive,
      maskLabel,
      scoringMaskActive: maskOpen,
    }),
    [viewedTree, index, lens, maskActive, maskLabel, maskOpen],
  );

  const addressing = useMemo<TreeAddressing>(
    () => ({ viewedAddress, viewedMask, viewedMoment, resting }),
    [viewedAddress, viewedMask, viewedMoment, resting],
  );

  return (
    <TreeAddressingContext.Provider value={addressing}>
      <ViewedLineageContext.Provider value={viewed}>{children}</ViewedLineageContext.Provider>
    </TreeAddressingContext.Provider>
  );
}

export function useViewedLineage(): ViewedLineage {
  const ctx = useContext(ViewedLineageContext);
  if (ctx === null) throw new Error("useLineage* must be used within a LineageProvider");
  return ctx;
}

// The viewed leaf's course entry: its `state` is the one word and next step every surface reads.
// That state is the head's under any mask or moment, so the last landing's stands across a re-key.
export function useViewedCourse(): HierarchyCourse | null {
  const { index } = useViewedLineage();
  const { viewedPath } = useWorkspace();
  const landed = useMemo(() => courseAt(index, viewedPath), [index, viewedPath]);
  const [kept, setKept] = useState(landed);
  if (landed !== null && landed !== kept) setKept(landed);
  const address = viewedPath ? encodeCyclePath(viewedPath) : null;
  return landed ?? (kept?.address === address ? kept : null);
}

// A sidebar row's campaign, by its address.
export function useLineageTree(address: string, enabled: boolean): CampaignTree {
  const ctx = useContext(TreeAddressingContext);
  if (ctx === null) throw new Error("useLineage* must be used within a LineageProvider");
  const viewed = address === ctx.viewedAddress;
  return useTree(
    enabled ? address : null,
    viewed ? ctx.viewedMask : null,
    viewed ? ctx.viewedMoment : null,
    ctx.resting,
  );
}

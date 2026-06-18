// The single searchpoint resolver behind the connector control panel's preview.
// One shape, three contexts: a draft in origin setup, a live in-flight
// candidate, or the dataset origin — resolved by priority so the panel always
// shows the searchpoint that's actually relevant right now (was hard-wired to
// the dataset origin in all three states). Pure data → data, mirrors
// `candidateSearchPoint.ts`; reuses its normalized `CandidateSearchPoint` shape.

import { liveL1InputCandidates, roundOf, type DashboardSnapshot } from "@/lib/poll";
import { candidateLabel } from "@/lib/candidate-label";
import { searchPoint, type CandidateSearchPoint } from "./candidateSearchPoint";
import type { ConnectorView } from "@/lib/types";
import type { DraftCampaignWire } from "@/lib/api";

export type SearchPointMode = "draft" | "live" | "origin";

export interface ResolvedSearchPoint {
  point: CandidateSearchPoint;
  mode: SearchPointMode;
  // Human label for the preview header sub-line, so the operator sees WHICH
  // searchpoint they're looking at (setup / live candidate N / origin).
  label: string;
}

// The in-flight candidate = the latest-seeded l1 input candidate (highest idx)
// in the live round. Returns null between rounds and in L2/L3 phases, where no
// l1 input candidates exist (the caller then falls back to the origin).
export function liveInFlightSearchPoint(
  dash: DashboardSnapshot | null,
): { point: CandidateSearchPoint; label: string } | null {
  const candidates = liveL1InputCandidates(dash);
  if (candidates.length === 0) return null;
  let latest = candidates[0];
  for (const c of candidates) {
    if (Number(c.idx ?? -1) > Number(latest.idx ?? -1)) latest = c;
  }
  return {
    point: searchPoint(latest.prompt_fields, latest.pp_override),
    label: latest.label || candidateLabel(roundOf(dash), latest.idx),
  };
}

// Resolve the searchpoint the connector panel should preview. Priority:
//   1. a draft in origin setup → the draft's searchpoint (mode "draft")
//   2. a live run with an in-flight candidate → that candidate (mode "live")
//   3. otherwise → the dataset origin from the connector view (mode "origin")
export function resolveSearchPoint({
  draft,
  dash,
  cv,
}: {
  draft: DraftCampaignWire | null;
  dash: DashboardSnapshot | null;
  cv: ConnectorView;
}): ResolvedSearchPoint {
  if (draft) {
    return {
      point: searchPoint(draft.origin_prompt_fields, draft.pipeline_overlay),
      mode: "draft",
      label: "draft — setup",
    };
  }
  if (cv.isLive) {
    const live = liveInFlightSearchPoint(dash);
    if (live) {
      return { point: live.point, mode: "live", label: `live — ${live.label}` };
    }
  }
  return {
    point: searchPoint(cv.originPromptFields, {}),
    mode: "origin",
    label: cv.isLive ? "read-only — running" : "read-only",
  };
}

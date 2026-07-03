"use client";
// The hard-samples surface for an L4 self-optimization (pp-self) unit. A pp-self
// outer cycle has no `cache.json` roster — its "samples" are inner campaigns, not
// scored rows — so the heat-map / hardness leaderboard have nothing to render and
// would otherwise be silently absent. Instead of a blank, this points the operator
// at where the real per-sample backend / sample-map / hardness live: the INNER
// run. It unifies with the sidebar inner-loop drill-in — same `useInnerCycles`
// source, same `selectCyclePath([outer, inner])` action the loved button uses — so
// there is one way to open an inner run, surfaced in two places.

import { useWorkspace } from "@/lib/workspace";
import { useInnerCycles } from "@/lib/hooks/useInnerCycles";

export function SelfOptSamplesPointer() {
  const { campaignId, cycleId, selectCyclePath } = useWorkspace();
  const { activeInnerCampaignId, activeInnerCycleId } = useInnerCycles(
    campaignId,
    cycleId,
    true,
  );
  const hasLiveInner =
    !!campaignId && !!cycleId && !!activeInnerCampaignId && !!activeInnerCycleId;

  return (
    <div className="hs-heat-wrap hs-selfopt-note">
      <p className="hs-selfopt-lead">
        Self-optimization run — the per-sample backend, sample map and hardness
        leaderboard live in the <strong>inner run</strong>.
      </p>
      {hasLiveInner ? (
        <button
          type="button"
          className="hs-selfopt-open"
          onClick={() =>
            selectCyclePath([
              { campaignId: campaignId!, cycleId: cycleId! },
              { campaignId: activeInnerCampaignId!, cycleId: activeInnerCycleId! },
            ])
          }
        >
          Open the live inner run →
        </button>
      ) : (
        <p className="hs-selfopt-hint">
          Expand this campaign in the sidebar to open a finished inner run.
        </p>
      )}
    </div>
  );
}

"use client";
// The hard-samples surface for an L4 self-optimization (pp-self) unit. A pp-self
// outer cycle has no `cache.json` roster — its "samples" are inner campaigns, not
// scored rows — so the heat-map / hardness leaderboard have nothing to render and
// would otherwise be silently absent. Instead of a blank, this points the operator
// at where the real per-sample backend / sample-map / hardness live: the INNER
// run. It unifies with the sidebar inner-loop drill-in — same `useForest` source,
// same `drillInto` action the sidebar row and the L4 panel rows fire — so there is
// one way to open an inner run, surfaced in three places.

import { useWorkspace } from "@/lib/workspace";
import { useForest } from "@/lib/hooks/useForest";

export function SelfOptSamplesPointer() {
  const { viewedPath, drillInto } = useWorkspace();
  // The viewed course's own sandbox — the same address, and now the same shared
  // poll, the sidebar and the candidates card read it at.
  const forest = useForest(viewedPath ?? [], viewedPath != null);

  // Liveness has ONE server-owned answer — `run_phase` — so read it off the
  // POINTED cycle rather than trusting the pointer's existence. The pointer file
  // is freshness-blind and never cleared on death (it's written once at
  // inner-cycle start), so a finished producer still names its last inner run;
  // without this check the button would offer to open a run that has stopped.
  const live = forest.cycles.find(
    (c) =>
      c.campaign_id === forest.activeCampaignId &&
      c.cycle_id === forest.activeCycleId &&
      c.run_phase === "running",
  );

  return (
    <div className="hs-heat-wrap hs-selfopt-note">
      <p className="hs-selfopt-lead">
        Self-optimization run — the per-sample backend, sample map and hardness
        leaderboard live in the <strong>inner run</strong>.
      </p>
      {live ? (
        <button
          type="button"
          className="hs-selfopt-open"
          onClick={() => drillInto(live.campaign_id, live.cycle_id)}
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

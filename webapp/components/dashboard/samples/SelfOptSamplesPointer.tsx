"use client";
// The hard-samples surface for an L4 self-optimization (pp-self) unit. A pp-self
// outer cycle has no `cache.json` roster — its "samples" are inner campaigns, not
// scored rows — so the heat-map / hardness leaderboard have nothing to render and
// would otherwise be silently absent. Instead of a blank, this points the operator
// at where the real per-sample backend / sample-map / hardness live: the INNER
// run, opened by the same `drillInto` hop the sidebar row and the L4 panel rows
// fire — one way to open an inner run, surfaced in three places.

import { useWorkspace } from "@/lib/workspace";
import { useViewedLineage } from "@/lib/lineage";
import { childCourses, pathOf } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";

export function SelfOptSamplesPointer() {
  const { viewedPath, drillInto } = useWorkspace();
  // The ONE served tree's index, addressed at the viewed course by OWN-path candidates
  // rather than a course lookup: when the viewed course is a FORK there is no course node
  // to read at all (a fork is dissolved onto the parent's timeline), so its attempts must
  // be addressed by their own path.
  const { index } = useViewedLineage();

  // Liveness has ONE server-owned answer — `run_phase` — so the live run is simply the
  // inner course reporting `running`. This used to intersect that with the sandbox's
  // `active_cycle_id` pointer, which is written once at inner-cycle start and never
  // cleared on death: freshness-blind, so it could only ever narrow a `run_phase` the
  // server already owns, or contradict it. The tree serves `run_phase` per course; the
  // loop runs one inner cycle at a time. So the pointer was the redundant half.
  const live = viewedPath
    ? (index.get(encodeCyclePath(viewedPath))?.candidates ?? [])
        .flatMap(childCourses)
        .find((c) => c.run_phase === "running")
    : undefined;
  const at = live ? pathOf(live).at(-1) : undefined;

  return (
    <div className="hs-heat-wrap hs-selfopt-note">
      <p className="hs-selfopt-lead">
        Self-optimization run — the per-sample backend, sample map and hardness
        leaderboard live in the <strong>inner run</strong>.
      </p>
      {at ? (
        <button
          type="button"
          className="hs-selfopt-open"
          onClick={() => drillInto(at.campaignId, at.cycleId)}
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

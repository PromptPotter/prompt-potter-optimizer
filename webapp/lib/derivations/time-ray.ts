import type { HierarchyStep, Said } from "@/lib/api/types";
import { pathOfAddress, type CyclePath } from "@/lib/ids";

export interface RayStep {
  // Never a window index, which shifts when a new cycle is discovered.
  key: string;
  path: CyclePath;
  pathKey: string;
  // In `path`'s OWN ledger: below the viewed course it is an address to navigate to, never a moment to fold to.
  offset: number;
  ts: string;
  // served: `StepFacts.gap_before_s`, heartbeats included
  gapBeforeS: number;
  cluster: number;
  activity: Said;
  round: number | null;
  arm: string | null;
}

// Only steps BELOW `rootPathKey` cluster: the root's own events are the story being told.
export function raySteps(served: readonly HierarchyStep[], rootPathKey: string): RayStep[] {
  const steps: RayStep[] = [];

  for (const step of served) {
    const { activity, offset, ts, gap_before_s, round, arm } = step.facts;
    const pathKey = step.parent;
    const prev = steps[steps.length - 1];
    const address = { activity, round, arm };

    if (prev && prev.pathKey === pathKey && pathKey !== rootPathKey) {
      steps[steps.length - 1] = {
        ...prev,
        offset,
        ts,
        cluster: prev.cluster + 1,
        ...address,
      };
      continue;
    }

    steps.push({
      key: step.address,
      path: pathOfAddress(pathKey),
      pathKey,
      offset,
      ts,
      gapBeforeS: gap_before_s,
      cluster: 1,
      ...address,
    });
  }
  return steps;
}

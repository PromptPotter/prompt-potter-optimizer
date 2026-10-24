import type { ActivityItem, HierarchyStep, ProducerReading } from "@/lib/api/types";
import type { RunPhase, RunStatus } from "@/lib/api/types.generated";
import { fmtDuration } from "@/lib/format";
import { pathOfAddress, type CyclePath } from "@/lib/ids";
import { STATUS_MARK, phaseParked, phaseWalks, type RunPhaseTone } from "@/lib/run-phase";

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
  activity: ActivityItem;
  round: number | null;
  arm: string | null;
}

// Only steps BELOW `rootPathKey` cluster: the root's own events are the story being told.
export function raySteps(served: readonly HierarchyStep[], rootPathKey: string): RayStep[] {
  const steps: RayStep[] = [];

  for (const step of served) {
    const { activity, offset, ts, gap_before_s } = step.facts;
    const pathKey = step.parent;
    const prev = steps[steps.length - 1];
    const address = { activity, round: activity.round, arm: activity.arm };

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

export interface RayHead {
  tone: RunPhaseTone;
  wedged: boolean;
  label: string;
  detail: string;
  target: CyclePath | null;
}

export function rayHead(
  steps: readonly RayStep[],
  served: readonly HierarchyStep[],
  run: { run_phase: RunPhase; status: RunStatus; producer: ProducerReading } | null,
  rootPathKey: string,
  // served: `dashboard.json::waiting_on`
  waitingOn: string | null,
): RayHead | null {
  if (run === null) return null;
  const newestStep = steps[steps.length - 1];
  const target = newestStep && newestStep.pathKey !== rootPathKey ? newestStep.path : null;

  const { run_phase: runPhase, status, producer } = run;
  const tone = STATUS_MARK[status.mark].tone;
  if (!phaseWalks(runPhase)) {
    return {
      tone,
      wedged: false,
      label: status.label,
      detail: phaseParked(runPhase, producer.attached),
      target: null,
    };
  }

  if (producer.stalled) {
    return { tone, wedged: true, label: producer.label, detail: producer.stalled, target };
  }
  const recent = producer.stepping;
  if (!recent && waitingOn && producer.open_for_s != null) {
    return {
      tone,
      wedged: false,
      label: producer.label,
      detail: `${waitingOn} · open ${fmtDuration(producer.open_for_s)}`,
      target,
    };
  }

  // The root's newest line still reads `running`: its call is open, and a child holds the head.
  const rootNewest = served.findLast((s) => s.parent === rootPathKey);
  if (rootNewest?.facts.activity.kind === "running" && target) {
    return {
      tone: "quiet",
      wedged: false,
      label: producer.label_on_child,
      detail: newestStep?.activity.label ?? "on a child run",
      target,
    };
  }

  return {
    tone,
    wedged: false,
    label: producer.label,
    detail: newestStep && recent ? newestStep.activity.label : "no recent step",
    target,
  };
}

import type { ActivityItem, ProducerReading, RayItem } from "@/lib/api/types";
import type { RunPhase, RunStatus } from "@/lib/api/types.generated";
import { fmtDuration } from "@/lib/format";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import { STATUS_MARK, phaseParked, phaseWalks, type RunPhaseTone } from "@/lib/run-phase";

export interface RayStep {
  // Never a window index, which shifts when a new cycle is discovered.
  key: string;
  path: CyclePath;
  pathKey: string;
  // In `path`'s OWN ledger: below the viewed course it is an address to navigate to, never a moment to fold to.
  offset: number;
  ts: string;
  // served: `RayItem.gap_before_s`, heartbeats included
  gapBeforeS: number;
  cluster: number;
  activity: ActivityItem;
  round: number | null;
  candidateLabel: string | null;
}

function toPath(item: RayItem): CyclePath {
  return item.path.map((h) => ({ campaignId: h.campaign_id, cycleId: h.cycle_id }));
}

// Only steps BELOW `rootPathKey` cluster: the root's own events are the story being told.
export function raySteps(items: readonly RayItem[], rootPathKey: string): RayStep[] {
  const steps: RayStep[] = [];

  for (const item of items) {
    // A bare heartbeat ends a silence without becoming a step.
    const activity = item.activity;
    if (!activity) continue;

    const path = toPath(item);
    const pathKey = encodeCyclePath(path);
    const prev = steps[steps.length - 1];
    const address = { activity, round: activity.round, candidateLabel: activity.candidate };

    if (prev && prev.pathKey === pathKey && pathKey !== rootPathKey) {
      steps[steps.length - 1] = {
        ...prev,
        offset: item.offset,
        ts: item.ts,
        cluster: prev.cluster + 1,
        ...address,
      };
      continue;
    }

    steps.push({
      key: `${pathKey}#${item.offset}`,
      path,
      pathKey,
      offset: item.offset,
      ts: item.ts,
      gapBeforeS: item.gap_before_s,
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
  items: readonly RayItem[],
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
  const rootNewest = items.findLast(
    (i) => i.activity !== null && encodeCyclePath(toPath(i)) === rootPathKey,
  );
  if (rootNewest?.activity?.kind === "running" && target) {
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

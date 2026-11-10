import { useCallback, useMemo } from "react";
import { failurePasses } from "./api/client";
import { dashboardRead, earlierStepsRead, onViewedPhaseChange, stepsRead } from "./api/reads";
import { DASHBOARD_LIVE_POLL_MS, RECONNECT_POLL_MS } from "./cadence";
import { phaseIs } from "./derivations/run-phase";
import { encodeCyclePath, type CyclePath } from "./ids";
import type {
  HierarchyStep,
  HierarchySteps,
  Said,
  ServedDashboard,
} from "@/lib/api/types";
import { shownData, useRead } from "./hooks/useRead";
import { invalidateReads, type ReadFailure, type ReadResult } from "./read-cache";
import { seedScoringMask } from "./scoring-mask";
import { createModuleStore } from "./state/module-store";
import { useMomentAt, useWorkspace } from "./workspace";

export const DASHBOARD_PENDING = "Waiting for this run’s dashboard…";

// Whose read failed: the viewed cycle's, pinned or followed, or with no cycle in view the reads that
// list and point at runs; `left` is the workspace just leaving a gone pin, whatever is read now.
export type FailureOf = "pinned" | "followed" | "listing" | "left";

// The CONNECTION's own row, never the run's (that is the served `dash.said`): the one `Said` the
// client makes, off the failed read itself. Only a transient failure is the server unreachable;
// an answered refusal says its own sentence, and one that passes is asked again. A 404 says a
// campaign is gone on the viewed cycle's read alone, and the run is returned to only off a pin
// (`workspace.tsx::reportAddressGone`): a followed run gone stays in view until the pointer moves.
export function connectionSaid(failure: ReadFailure | null, of: FailureOf): Said | null {
  const gone = failure?.kind === "gone" && of !== "listing";
  if (of === "left" || gone) {
    return {
      key: "address_gone",
      tone: "attention",
      label: "This campaign no longer exists",
      value: null,
      sub: null,
      detail: `It was deleted, or its store was reset.${of === "followed" ? "" : " Returning to the active run."}`,
    };
  }
  if (failure === null) return null;
  const offline = failure.kind === "transient";
  return {
    key: "connection_lost",
    tone: "danger",
    label: offline ? "PromptPotter API unreachable" : failure.message,
    value: null,
    sub: null,
    detail: !failurePasses(failure.kind)
      ? null
      : offline
        ? "Reconnecting — check the server is running."
        : "Retrying automatically.",
  };
}

interface DashboardPoll {
  read: ReadResult<ServedDashboard>;
  // The read's body, or the unit's last one while a moved moment loads; null once the unit is gone.
  dash: ServedDashboard | null;
  isLive: boolean;
}

// A cycle's dashboard poll: every caller naming one unit and moment shares one read, at the most
// eager caller's `intervalMs` (null reads once). Every caller of the viewed cycle's carries its
// phase re-ask, so the read's change effect is that whoever subscribed last
// (`read-cache.ts::descriptorOf`).
export function useDashboard(path: CyclePath | null, intervalMs: number | null): DashboardPoll {
  const { reportAddressGone, viewed } = useWorkspace();
  const at = useMomentAt(path);
  const unit = path ? encodeCyclePath(path) : null;
  const isViewed = unit !== null && unit === viewed?.address;
  const read = useRead(
    path
      ? {
          ...dashboardRead(path, at, intervalMs),
          ...(isViewed ? { onChange: onViewedPhaseChange } : {}),
          onGone: () => reportAddressGone(encodeCyclePath(path)),
        }
      : null,
  );
  const gone = read.status === "failed" && read.failure.kind === "gone";
  const dash = gone ? null : shownData(read);
  const isLive = at === null && read.status !== "failed" && dash?.producer.appending === true;
  return useMemo(() => ({ read, dash, isLive }), [read, dash, isLive]);
}

// The viewed cycle's beat, for its dashboard and the steps beside it: a replayed moment, or a
// lost read, polls slow, since a fold at a past offset is immutable.
export function viewedIntervalMs(at: number | null, lost: boolean): number {
  return at !== null || lost ? RECONNECT_POLL_MS : DASHBOARD_LIVE_POLL_MS;
}

// The viewed cycle's dashboard, and the one place its scoring mask is seeded from: keyed on the
// address whose dashboard it read, so an inner run drilled into seeds its own criterion. A lost
// read slows on the cache's own floor (`read-cache.ts::intervalOf`).
export function useViewedDashboard(): DashboardPoll {
  const { viewed, at } = useWorkspace();
  const poll = useDashboard(viewed?.path ?? null, viewedIntervalMs(at, false));
  if (viewed !== null && poll.dash !== null) seedScoringMask(viewed.address, poll.dash);
  return poll;
}

const RAY_WINDOW = 200;

// How far back the operator paged the viewed course: naming no view, it outlives the ray's mount;
// the pages themselves are `earlierStepsRead`'s, in the cache beside the head window.
const paging = createModuleStore<{ unit: string | null; anchor: string | null; pages: number }>({
  unit: null,
  anchor: null,
  pages: 0,
});

interface TimeRay {
  steps: HierarchyStep[];
  head: ReadResult<HierarchySteps>;
  earlier: ReadResult<HierarchySteps>;
  hasMore: boolean;
  loadOlder: () => void;
}

// The viewed course's steps: a head window on its dashboard's beat, then pages asked one by one. A
// settled run's steps move only on a resume, whose phase change re-asks them
// (`reads.ts::onViewedPhaseChange`).
export function useTimeRay(): TimeRay {
  const { viewed, at } = useWorkspace();
  const path = viewed?.path ?? null;
  const { read: board, dash } = useViewedDashboard();
  const lost = board.status === "failed" && board.failure.kind !== "gone";
  const intervalMs = phaseIs(dash?.run_phase, "settled") ? null : viewedIntervalMs(at, lost);
  const unit = viewed?.address ?? null;
  const head = useRead(path ? stepsRead(path, RAY_WINDOW, null, intervalMs) : null);
  const headWindow = shownData(head);
  const paged = paging.useStore();
  const own = paged.unit === unit && paged.anchor !== null ? paged : null;
  const earlier = useRead(
    path && own?.anchor ? earlierStepsRead(path, RAY_WINDOW, own.anchor, own.pages) : null,
  );
  const earlierWindow = shownData(earlier);
  const cursor = own ? (earlierWindow?.cursor_prev ?? null) : (headWindow?.cursor_prev ?? null);
  const earlierStatus = earlier.status;
  const loadOlder = useCallback(() => {
    // The same pages asked again re-ask the read; one more page names a new one.
    if (earlierStatus === "failed") invalidateReads("earlier-steps");
    else if (earlierStatus === "loading" || cursor === null) return;
    else if (own) paging.set({ pages: own.pages + 1 });
    else paging.set({ unit, anchor: cursor, pages: 1 });
  }, [earlierStatus, cursor, own, unit]);

  const earlierSteps = earlierWindow?.steps;
  const headSteps = headWindow?.steps;
  const steps = useMemo(
    () => [...(earlierSteps ?? []), ...(headSteps ?? [])],
    [earlierSteps, headSteps],
  );
  return { steps, head, earlier, hasMore: cursor !== null, loadOlder };
}

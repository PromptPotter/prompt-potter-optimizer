import { useCallback, useMemo } from "react";
import { failurePasses } from "./api/client";
import { dashboardRead, earlierStepsRead, stepsRead } from "./api/reads";
import { encodeCyclePath, type CyclePath } from "./ids";
import type {
  HierarchyStep,
  HierarchySteps,
  Said,
  ServedDashboard,
  WarmingDashboard,
} from "@/lib/api/types";
import { shownData, useRead } from "./hooks/useRead";
import { reaskRead, type ReadFailure, type ReadResult } from "./read-cache";
import { seedScoringMask } from "./scoring-mask";
import { createModuleStore } from "./state/module-store";
import { useMomentAt, useWorkspace, type GoneAddress } from "./workspace";
import type { AddressRefusal } from "./address";

export const DASHBOARD_PENDING = "Waiting for this run’s dashboard…";

// Whose read failed: the viewed cycle's, pinned or followed, or with no cycle in view the reads that
// list and point at runs; a `GoneAddress` cause is the workspace's own notice, whatever is read now.
export type FailureOf = "pinned" | "followed" | "listing" | GoneAddress["cause"];

const REFUSED_ADDRESS: Record<AddressRefusal, Pick<Said, "label" | "detail">> = {
  retired: {
    label: "This link is from an earlier version",
    detail: "Links of the #/c/… form no longer open: find its campaign in the list.",
  },
  malformed: {
    label: "This link opens nothing",
    detail: "Its address is not one this version reads.",
  },
};

// The CONNECTION's own row, never the run's (that is the served `dash.said`): the one `Said` the
// client makes, off the failed read itself. Only a transient failure is the server unreachable;
// an answered refusal says its own sentence, and one that passes is asked again. A 404 says a
// campaign is gone on the viewed cycle's read alone, and the run is returned to only off a pin
// (`workspace.tsx::reportAddressGone`): a followed run gone stays in view until the pointer moves.
export function connectionSaid(failure: ReadFailure | null, of: FailureOf): Said | null {
  if (of === "retired" || of === "malformed") {
    return { key: "address_refused", tone: "attention", value: null, sub: null, ...REFUSED_ADDRESS[of] };
  }
  const gone = failure?.kind === "gone" && of !== "listing";
  if (of === "gone" || gone) {
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

// A cycle's dashboard: every caller naming one unit and moment shares one read, re-asked when the
// feed says its store moved.
export function useDashboard(path: CyclePath | null): DashboardPoll {
  const { reportAddressGone } = useWorkspace();
  const at = useMomentAt(path);
  const read = useRead<ServedDashboard, ServedDashboard | WarmingDashboard>(
    path
      ? { ...dashboardRead(path, at), onGone: () => reportAddressGone(encodeCyclePath(path)) }
      : null,
  );
  const gone = read.status === "failed" && read.failure.kind === "gone";
  const dash = gone ? null : shownData(read);
  const isLive = at === null && read.status !== "failed" && dash?.producer.appending === true;
  return useMemo(() => ({ read, dash, isLive }), [read, dash, isLive]);
}

// The viewed cycle's dashboard, and the one place its scoring mask is seeded from: keyed on the
// address whose dashboard it read, so an inner run drilled into seeds its own criterion.
export function useViewedDashboard(): DashboardPoll {
  const { viewed } = useWorkspace();
  const poll = useDashboard(viewed?.path ?? null);
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

// The viewed course's steps: a head window the feed moves, then pages asked one by one.
export function useTimeRay(): TimeRay {
  const { viewed } = useWorkspace();
  const path = viewed?.path ?? null;
  const unit = viewed?.address ?? null;
  const head = useRead(path ? stepsRead(path, RAY_WINDOW, null) : null);
  const headWindow = shownData(head);
  const paged = paging.useStore();
  const own = paged.unit === unit && paged.anchor !== null ? paged : null;
  const earlierAsk = path && own?.anchor ? earlierStepsRead(path, RAY_WINDOW, own.anchor, own.pages) : null;
  const earlier = useRead(earlierAsk);
  const earlierId = earlierAsk?.id ?? null;
  const earlierWindow = shownData(earlier);
  const cursor = own ? (earlierWindow?.cursor_prev ?? null) : (headWindow?.cursor_prev ?? null);
  const earlierStatus = earlier.status;
  const loadOlder = useCallback(() => {
    // The same pages asked again re-ask the read; one more page names a new one.
    if (earlierStatus === "failed") {
      if (earlierId !== null) reaskRead(earlierId);
    } else if (earlierStatus === "loading" || cursor === null) return;
    else if (own) paging.set({ pages: own.pages + 1 });
    else paging.set({ unit, anchor: cursor, pages: 1 });
  }, [earlierStatus, earlierId, cursor, own, unit]);

  const earlierSteps = earlierWindow?.steps;
  const headSteps = headWindow?.steps;
  const steps = useMemo(
    () => [...(earlierSteps ?? []), ...(headSteps ?? [])],
    [earlierSteps, headSteps],
  );
  return { steps, head, earlier, hasMore: cursor !== null, loadOlder };
}

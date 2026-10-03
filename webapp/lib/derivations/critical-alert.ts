// The one verdict for the loud cross-tab alert bar, over signals AppShell already reconciled.
// Branch order IS the precedence.

import { STOP_REASON_LABELS } from "@/lib/api/types.generated";
import type { RoundSummary } from "@/lib/api/types";
import type { DashboardSnapshot, StatusKind } from "@/lib/poll";

export interface CriticalAlert {
  // `info` = nothing is wrong: the app already recovered and says why.
  severity: "critical" | "warn" | "info";
  title: string;
  detail?: string;
  // The run never auto-pauses on a broken measurement; the operator decides.
  action?: "pause";
}

interface Args {
  bannerStatus: StatusKind;
  bannerText: string;
  bannerHint?: string;
  // Not a `StatusKind`: the poll's resting `INITIAL_STATE.status` is already `offline`, so only
  // the caller knows the cycle list loaded AND came back empty.
  emptyWorkspace?: boolean;
  dash: DashboardSnapshot | null;
  // The same reachability as the ConnectorInspector LED; this bar is its cross-tab twin.
  connectorDown?: boolean;
  connectorName?: string | null;
  connectorDetail?: string | null;
  // No slot free — the caller's own runs count too.
  machineBusy?: boolean;
  machineBusyHolder?: string | null;
  machineBusySince?: string | null;
  // `/machine-status::queue[].position` of the caller's earliest waiting launch, null when none.
  queuePosition?: number | null;
}

export function criticalAlert({
  bannerStatus,
  bannerText,
  bannerHint,
  emptyWorkspace,
  dash,
  connectorDown,
  connectorName,
  connectorDetail,
  machineBusy,
  machineBusyHolder,
  machineBusySince,
  queuePosition,
}: Args): CriticalAlert | null {
  // GONE outranks a crash: any `dash` in hand describes a campaign no longer on disk.
  if (bannerStatus === "gone") {
    return { severity: "info", title: bannerText, detail: bannerHint };
  }
  // Arrives wearing the poll's resting `offline`; the sidebar's empty state is the message.
  if (emptyWorkspace) return null;
  const err = dash?.error;
  if (err) {
    // Never a hardcoded "crashed": `STOP_REASON_INFO` separates a crash from a designed refusal.
    const label = (err.stop_reason && STOP_REASON_LABELS[err.stop_reason]) || "Run stopped";
    return {
      severity: "critical",
      title: `${label} — ${err.kind}`,
      detail: `stop: ${err.stop_reason}`,
    };
  }
  // The poll and AppShell already collapse every offline-class condition into this one.
  if (bannerStatus === "offline") {
    return { severity: "critical", title: bannerText, detail: bannerHint };
  }
  if (connectorDown) {
    return {
      severity: "critical",
      title: `Backend unreachable — ${connectorName ?? "connector"}`,
      detail: connectorDetail ?? undefined,
    };
  }
  if (queuePosition != null) {
    return {
      severity: "warn",
      title:
        queuePosition === 1
          ? "Queued — next in line"
          : `Queued — position ${queuePosition}`,
      detail: "it starts by itself when a slot frees",
    };
  }
  // A notice, not a refusal: pressing Start joins the queue.
  if (machineBusy) {
    return {
      severity: "warn",
      title: `Machine full — ${machineBusyHolder ?? "another run"} is running`,
      detail: machineBusySince
        ? `a launch will queue · oldest run since ${machineBusySince}`
        : "a launch will queue",
    };
  }
  // Two orthogonal signals (server phase × this connection's freshness), never one "detached" phase.
  if (dash?.run_phase === "running" && bannerStatus === "stale") {
    return { severity: "warn", title: "Run went silent", detail: bannerText };
  }
  // Only `critical` health raises the bar; `degraded` is surfaced per round by `round-health.ts`.
  const latest = (dash?.rounds ?? []).reduce<RoundSummary | null>(
    (acc, r) => (acc === null || r.round >= acc.round ? r : acc),
    null,
  );
  if (latest?.health?.grade === "critical") {
    return {
      severity: "critical",
      title:
        latest.round === 0
          ? "Degraded origin — pipeline may be structurally broken"
          : `Round ${latest.round} degraded — pipeline may be structurally broken`,
      detail: latest.health.suggested_action ?? undefined,
      action: "pause",
    };
  }
  // `warming_up` is not a lost connection; the masthead's STATE chip carries it.
  return null;
}

// Single source of truth for the loud, cross-tab critical-alert bar. The
// dashboard already surfaced failures three quiet ways — an inline
// RunErrorBanner (dashboard tab only), a floating chip that auto-hid after
// 10 s, and an always-green phase tag — none of which an operator who has
// alt-tabbed away would notice. This collapses the "something is wrong"
// question to one boolean-ish verdict the top banner renders on every tab.
//
// Inputs are already-reconciled signals owned by AppShell (the same
// `bannerStatus`/`bannerText`/`bannerHint` it feeds the StatusAssistant, plus
// `dash` and the connection-aware `runPhaseResolved`) — no new state, no new
// poll. Reader-side and pure, so it sits in the Vitest derivation scope.

import type { RoundSummary } from "@/lib/api/types";
import type { DashboardSnapshot, StatusKind } from "@/lib/poll";

export interface CriticalAlert {
  // `critical` = blocking failure (server gone, run crashed); `warn` = the run
  // is technically alive but its producer went silent. Drives tone + a11y
  // live-region politeness.
  severity: "critical" | "warn";
  title: string;
  detail?: string;
  // When set, the banner offers a one-click operator action. `"stop"` = the
  // run is alive but its measurement is structurally broken — surface a
  // "Stop campaign" button (the run never auto-stops; the operator decides).
  action?: "stop";
}

interface Args {
  bannerStatus: StatusKind;
  bannerText: string;
  bannerHint?: string;
  dash: DashboardSnapshot | null;
  runPhaseResolved: string | null;
  // The SAME connector reachability the ConnectorInspector LED uses (`health != null
  // && health.status !== "live"`), reconciled by the caller from `useConnector()`.
  // The backend is the run's dependency — when its probe goes red, this banner
  // is the loud, cross-tab twin of the LED. `connectorName`/`connectorDetail`
  // come straight from the same `ConnectorView`.
  connectorDown?: boolean;
  connectorName?: string | null;
  connectorDetail?: string | null;
  // Another user holds the single sequential run slot (the server runs one
  // campaign at a time). `machineBusyHolder` is their label for the detail
  // line; both come straight from the `/machine-status` poll
  // (`useMachineStatus`). The always-on twin of the 409 a launch returns.
  machineBusy?: boolean;
  machineBusyHolder?: string | null;
  machineBusySince?: string | null;
}

export function criticalAlert({
  bannerStatus,
  bannerText,
  bannerHint,
  dash,
  runPhaseResolved,
  connectorDown,
  connectorName,
  connectorDetail,
  machineBusy,
  machineBusyHolder,
  machineBusySince,
}: Args): CriticalAlert | null {
  // Crash/abort wins — a terminal run with a projected ErrorRecord is the most
  // actionable failure. The full multi-line message + remediation stays in the
  // dashboard-tab RunErrorBanner; here we show only the can't-miss headline.
  const err = dash?.error;
  if (err) {
    return {
      severity: "critical",
      title: `Run crashed — ${err.kind}`,
      detail: `stop: ${err.stop_reason}`,
    };
  }
  // Every offline-class condition (fetch failure, stamp mismatch, no wallclock,
  // empty workspace) is already collapsed to `offline` by the poll + the
  // AppShell reconciliation, so this one branch covers them all.
  if (bannerStatus === "offline") {
    return { severity: "critical", title: bannerText, detail: bannerHint };
  }
  // Backend dependency down — the loud twin of the ConnectorInspector LED. The
  // PromptPotter API is up (else `offline` won above and the health probe would
  // be null anyway), but the backend it scores through fails its `/health`
  // probe, so the run can make no progress.
  if (connectorDown) {
    return {
      severity: "critical",
      title: `Backend unreachable — ${connectorName ?? "connector"}`,
      detail: connectorDetail ?? undefined,
    };
  }
  // Another user holds the single sequential run slot. Not a failure of this
  // operator's run — but it blocks them from launching (the server runs one
  // campaign at a time), so it's a can't-miss "wait your turn" headline, the
  // always-on twin of the 409 a launch attempt returns. After connectorDown so
  // a genuine dependency failure still wins.
  if (machineBusy) {
    return {
      severity: "critical",
      title: `Machine busy — ${machineBusyHolder ?? "another user"} is running a campaign`,
      detail: machineBusySince
        ? `the machine processes one at a time · since ${machineBusySince}`
        : "the machine processes one campaign at a time",
    };
  }
  // Producer went quiet: declared `running` but the poll has gone stale, so the
  // connection-aware phase resolves to `detached`. Not fatal, but the operator
  // should know writes stopped.
  if (runPhaseResolved === "detached") {
    return { severity: "warn", title: "Run went silent", detail: bannerText };
  }
  // Degradation verdict — the run is alive and writing, but its latest round's
  // measurement is structurally broken (backend-graded `critical`: e.g. a node
  // failing on most samples, or sustained degradation). Abort-worthy, so it
  // surfaces here as the loud cross-tab bar with a one-click stop. LOWEST
  // precedence — a genuine outage/crash above still wins. `healthy` is silent;
  // `degraded` stays quiet too (the run is fine to keep going) but IS surfaced —
  // as amber per-round notices under the Trend chart (`round-health.ts`), the
  // webapp twin of the CLI's yellow degraded line.
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
      action: "stop",
    };
  }
  // `warming_up` (server reachable, no snapshot yet — e.g. a forked cycle whose
  // runner hasn't started) is NOT a lost connection, so it raises nothing here.
  // The quiet StatusAssistant chip still surfaces "Origin running".
  return null;
}

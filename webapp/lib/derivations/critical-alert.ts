// Single source of truth for the loud, cross-tab critical-alert bar. The
// dashboard once surfaced failures quietly — an inline RunErrorBanner
// (dashboard tab only) and an always-green phase tag — neither of which an
// operator who has alt-tabbed away would notice. This collapses the
// "something is wrong" question to one boolean-ish verdict the top banner
// renders on every tab.
//
// Inputs are already-reconciled signals owned by AppShell (its
// `bannerStatus`/`bannerText`/`bannerHint`, plus `dash`) — no new state, no
// new poll. Reader-side and pure, so it sits in the Vitest derivation scope.

import { STOP_REASON_LABELS } from "@/lib/api/types.generated";
import type { RoundSummary } from "@/lib/api/types";
import type { DashboardSnapshot, StatusKind } from "@/lib/poll";

export interface CriticalAlert {
  // `critical` = blocking failure (server gone, run crashed); `warn` = the run
  // is technically alive but its producer went silent; `info` = nothing is
  // wrong and nothing is owed — the app already recovered and is saying why.
  // Drives tone + a11y live-region politeness.
  severity: "critical" | "warn" | "info";
  title: string;
  detail?: string;
  // When set, the banner offers a one-click operator action. `"pause"` = the
  // run is alive but its measurement is structurally broken — surface a
  // "Pause campaign" button (the run never auto-pauses; the operator decides).
  action?: "pause";
}

interface Args {
  bannerStatus: StatusKind;
  bannerText: string;
  bannerHint?: string;
  // No campaigns at all — a brand-new account, or one whose last campaign was
  // deleted. NOT a connection state and deliberately not a `StatusKind` member:
  // the poll cannot express it (its resting `INITIAL_STATE.status` is already
  // `offline`), so it arrives as its own fact rather than a fourth meaning of
  // `offline`. Reconciled by the caller, which is the only place that knows the
  // cycle list both loaded AND came back empty.
  emptyWorkspace?: boolean;
  dash: DashboardSnapshot | null;
  // The SAME connector reachability the ConnectorInspector LED uses (`health != null
  // && health.status !== "live"`), reconciled by the caller from `useConnector()`.
  // The backend is the run's dependency — when its probe goes red, this banner
  // is the loud, cross-tab twin of the LED. `connectorName`/`connectorDetail`
  // come straight from the same `ConnectorView`.
  connectorDown?: boolean;
  connectorName?: string | null;
  connectorDetail?: string | null;
  // Every run slot on the machine is taken — the caller's own run counts, so this
  // is "no slot free", not "someone else is running". `machineBusyHolder` labels the
  // oldest live run for the detail line; both come straight from the `/machine-status`
  // poll (`useMachineStatus`). The always-on twin of a launch that has to wait.
  machineBusy?: boolean;
  machineBusyHolder?: string | null;
  machineBusySince?: string | null;
  // Where the caller's own earliest waiting launch stands in the machine-wide drain
  // order (`/machine-status::queue[].position`), or null when they have none. It
  // outranks `machineBusy` below, because "yours is 2nd in line" is a strictly better
  // answer to "why can I not start" than "the box is full".
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
  // GONE outranks everything, including a crash. Any `dash` still in hand was
  // fetched before the address stopped existing, so its `error` describes a run
  // whose campaign is no longer on disk — announcing "Run crashed" there would
  // report a stale fact about a deleted thing. This branch is also not a failure:
  // the recovery has already happened and this only says why the view moved.
  if (bannerStatus === "gone") {
    return { severity: "info", title: bannerText, detail: bannerHint };
  }
  // Nothing here yet is not something wrong. A workspace with no campaigns
  // reaches this function wearing the poll's resting `offline`, so without its
  // own branch a first-run account gets the loudest bar the app has — and the
  // one surface that should say it already does, correctly, in the sidebar's
  // empty state. Silence here IS the message.
  if (emptyWorkspace) return null;
  // Crash/abort wins — a terminal run with a projected ErrorRecord is the most
  // actionable failure. The full multi-line message + remediation stays in the
  // dashboard-tab RunErrorBanner; here we show only the can't-miss headline.
  const err = dash?.error;
  if (err) {
    // The stop reason NAMES itself. `STOP_REASON_LABELS` is the generated mirror of
    // domain/phases.py::STOP_REASON_INFO, which already separates a crash from a DESIGNED
    // refusal — diverged, origin_gate, spend_budget and backend_unreachable are all
    // deliberate halts with a documented recovery. Hardcoding "Run crashed" reported every
    // one of them as the run falling over, which sends an operator hunting a bug that is not
    // there: a resume divergence rendered as `Run crashed — ResumeDivergenceError` when the
    // engine had refused, correctly, to mix two optimizers in one cycle. `run-phase.ts`
    // already reads this table; this surface restating the word was the drift.
    const label = (err.stop_reason && STOP_REASON_LABELS[err.stop_reason]) || "Run stopped";
    return {
      severity: "critical",
      title: `${label} — ${err.kind}`,
      detail: `stop: ${err.stop_reason}`,
    };
  }
  // Every offline-class condition (fetch failure, stamp mismatch, no wallclock)
  // is already collapsed to `offline` by the poll + the AppShell reconciliation,
  // so this one branch covers them all. An empty workspace is NOT one of them —
  // it returned above, because "nothing here yet" and "go check the server" are
  // two facts and this branch is the loud one.
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
  // The caller is IN the line. Not critical: nothing is broken, nothing was refused,
  // and the launch starts by itself — so it says where they stand rather than raising
  // an alarm about a machine that is working exactly as intended.
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
  // No run slot is free and the caller has nothing waiting. It no longer BLOCKS them —
  // pressing Start joins the queue — so this is the standing "you will wait" notice,
  // not a refusal. After connectorDown so a genuine dependency failure still wins.
  if (machineBusy) {
    return {
      severity: "warn",
      title: `Machine full — ${machineBusyHolder ?? "another run"} is running`,
      detail: machineBusySince
        ? `a launch will queue · oldest run since ${machineBusySince}`
        : "a launch will queue",
    };
  }
  // Producer went quiet: the server still declares `running`, but this
  // client's poll has gone stale (offline already returned above). Composed
  // from two orthogonal signals — `dash.run_phase` (server truth) and
  // `bannerStatus` (this connection's freshness) — never a single conflated
  // "detached" phase value. Not fatal, but the operator should know writes
  // may have stopped.
  if (dash?.run_phase === "running" && bannerStatus === "stale") {
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
      action: "pause",
    };
  }
  // `warming_up` (server reachable, no snapshot yet — e.g. a forked cycle whose
  // runner hasn't started) is NOT a lost connection, so it raises nothing here.
  // The CyclePicker's run-phase label ("scoring origin") surfaces this benign state.
  return null;
}

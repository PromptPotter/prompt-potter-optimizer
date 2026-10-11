import type { MachineNotice } from "@/lib/api/types";
import type { DashboardSnapshot, StatusKind } from "@/lib/poll";

export interface CriticalAlert {
  severity: "critical" | "warn" | "info";
  title: string;
  detail?: string;
  action?: "pause";
}

interface Args {
  bannerStatus: StatusKind;
  bannerText: string;
  bannerHint?: string;
  // Not a `StatusKind`: the poll rests at `offline`, so only the caller knows the list loaded empty.
  emptyWorkspace?: boolean;
  dash: DashboardSnapshot | null;
  connectorDown?: boolean;
  connectorName?: string | null;
  connectorDetail?: string | null;
  machineNotice?: MachineNotice | null;
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
  machineNotice,
}: Args): CriticalAlert | null {
  // Branch order IS the precedence. GONE first: any `dash` in hand describes a campaign no longer on disk.
  if (bannerStatus === "gone") {
    return { severity: "info", title: bannerText, detail: bannerHint };
  }
  // Arrives wearing the poll's resting `offline`, so it must precede that branch.
  if (emptyWorkspace) return null;
  const err = dash?.error;
  if (err) {
    return {
      severity: "critical",
      title: `${err.label} — ${err.kind}`,
      detail: err.next_step,
    };
  }
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
  if (machineNotice) return { severity: "warn", ...machineNotice };
  const stuck = dash?.producer.alert;
  if (stuck) return { severity: "warn", title: stuck.title, detail: stuck.detail };
  // Only a `critical` round carries an alert; `degraded` is `round-health.ts`'s.
  const latest = dash?.rounds.at(-1);
  if (latest?.health_alert) {
    return {
      severity: "critical",
      title: latest.health_alert,
      detail: latest.health?.suggested_action ?? undefined,
      action: "pause",
    };
  }
  return null;
}

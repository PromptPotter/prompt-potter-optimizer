import type { MachineNotice } from "@/lib/api/types";
import { STOP_REASON_INFO } from "@/lib/api/types.generated";
import type { ServedDashboard } from "@/lib/api";
import type { StatusKind } from "@/lib/poll";
import { producerAlert } from "./run-phase";

interface CriticalAlert {
  severity: "critical" | "warn" | "info";
  title: string;
  detail: string | null;
  action?: "pause";
}

interface Args {
  bannerStatus: StatusKind;
  bannerText: string;
  bannerHint: string;
  dash: ServedDashboard | null;
  connectorDown?: boolean;
  connectorName?: string | null;
  connectorDetail?: string | null;
  machineNotice?: MachineNotice | null;
}

export function criticalAlert({
  bannerStatus,
  bannerText,
  bannerHint,
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
  const err = dash?.error;
  if (err) {
    const info = STOP_REASON_INFO[err.stop_reason];
    return { severity: "critical", title: `${info.label} — ${err.kind}`, detail: info.next_step };
  }
  if (bannerStatus === "offline") {
    return { severity: "critical", title: bannerText, detail: bannerHint };
  }
  if (connectorDown) {
    return {
      severity: "critical",
      title: `Backend unreachable — ${connectorName ?? "connector"}`,
      detail: connectorDetail ?? null,
    };
  }
  if (machineNotice) return { severity: "warn", ...machineNotice };
  const stuck = dash ? producerAlert(dash.producer) : null;
  if (stuck) return { severity: "warn", title: stuck.title, detail: stuck.detail };
  // Only a `critical` round carries an alert; `degraded` is `round-health.ts`'s.
  const latest = dash?.rounds.at(-1);
  if (latest?.health_alert) {
    return {
      severity: "critical",
      title: latest.health_alert,
      detail: latest.health.suggested_action,
      action: "pause",
    };
  }
  return null;
}

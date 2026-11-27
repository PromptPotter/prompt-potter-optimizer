import type { ServedDashboard } from "@/lib/api/types";

export function roundOf(dash: ServedDashboard | null): number | null {
  return dash?.current_round.round ?? null;
}

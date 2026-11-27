import { useMemo } from "react";
import { resolveObserveSubject, type ObserveSubject } from "@/lib/derivations";
import { useViewedCycle } from "@/lib/state/lineage";
import { useViewedDashboard } from "@/lib/state/poll";
import { useSelection } from "@/lib/state/selection";

export function useObserveSubject(): ObserveSubject {
  const { dash } = useViewedDashboard();
  const standing = useViewedCycle()?.facts.standing ?? null;
  const { round, observe } = useSelection();
  return useMemo(
    () => resolveObserveSubject(dash, standing, { round, observe }),
    [dash, standing, round, observe],
  );
}

import { useMemo } from "react";
import { resolveObserveSubject, type ObserveSubject } from "@/lib/derivations";
import { useViewedCourse } from "@/lib/state/lineage";
import { useViewedDashboard } from "@/lib/state/poll";
import { useSelection } from "@/lib/state/selection";

export function useObserveSubject(): ObserveSubject {
  const { dash, isLive } = useViewedDashboard();
  const standing = useViewedCourse()?.facts.standing ?? null;
  const { round, observe } = useSelection();
  return useMemo(
    () => resolveObserveSubject(dash, standing, isLive, { round, observe }),
    [dash, standing, isLive, round, observe],
  );
}

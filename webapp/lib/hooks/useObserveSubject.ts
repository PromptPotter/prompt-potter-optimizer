"use client";

import { useMemo } from "react";
import { resolveObserveSubject, type ObserveSubject } from "@/lib/derivations";
import { useViewedCourse } from "@/lib/lineage";
import { useCycleStream } from "@/lib/poll";
import { useSelection } from "@/lib/SelectionContext";

export function useObserveSubject(): ObserveSubject {
  const { dash, isLive } = useCycleStream();
  const standing = useViewedCourse()?.facts.standing ?? null;
  const { candidate, round, observe } = useSelection();
  return useMemo(
    () => resolveObserveSubject(dash, standing, isLive, { candidate, round, observe }),
    [dash, standing, isLive, candidate, round, observe],
  );
}

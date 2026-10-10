"use client";

import { useMemo } from "react";
import { resolveObserveSubject, type ObserveSubject } from "@/lib/derivations";
import { useCycleStream } from "@/lib/poll";
import { useSelection } from "@/lib/SelectionContext";

export function useObserveSubject(): ObserveSubject {
  const { dash, isLive } = useCycleStream();
  const { candidate, round, observe } = useSelection();
  return useMemo(
    () => resolveObserveSubject(dash, isLive, { candidate, round, observe }),
    [dash, isLive, candidate, round, observe],
  );
}

"use client";
// `useCycleStream` plus `roundOf(dash)`, derived once for every consumer.

import { useMemo } from "react";
import { roundOf, useCycleStream, type CycleStreamState } from "@/lib/poll";

interface DashboardHookState extends CycleStreamState {
  dashRound: number | null;
}

export function useDashboard(): DashboardHookState {
  const state = useCycleStream();
  const dashRound = useMemo(() => roundOf(state.dash), [state.dash]);
  return { ...state, dashRound };
}

"use client";
// Single-ingress dashboard hook. Wraps `useCycleStream` and exposes the
// derived round number alongside the snapshot — every consumer that today
// re-derives `roundOf(dash)` independently should adopt this instead, so
// the derivation lives in one place and the component layer stays a
// pure renderer of `(dash, dashRound, isLive)`.
//
// `useCycleStream` itself stays as the lower-level primitive (it owns the
// poll lifecycle + payload-stamp guard). `useDashboard` is the "I just
// want the current dashboard" facade most components actually want.

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

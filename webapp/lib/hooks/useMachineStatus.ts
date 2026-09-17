"use client";
// Polls how full this machine is: how many campaigns it admits right now, how
// many are live, how deep the queue is, and where the caller's own waiting
// launches stand in it. A full box does not refuse — the CriticalAlertBanner
// says "you will wait", or names the position once they are in line. Counts the
// caller's own run too: occupancy is not relative to who asks.
//
// The read is `auth`-gated, so an anon preview never fires the identity-scoped probe and a
// session that dies halts the loop instead of 401-storming.

import { fetchMachineStatus, type MachineStatusResponse } from "@/lib/api";
import { useRead, type ReadResult } from "@/lib/hooks/useRead";

// Contention is rare-changing; a 5 s probe matches the connector
// reachability cadence and stays cheap (one tiny JSON read off the jobs dir).
const BUSY_INTERVAL_MS = 5000;

export function useMachineStatus(): ReadResult<MachineStatusResponse> {
  return useRead(
    { key: "machine-status", fetch: fetchMachineStatus },
    { surface: "machine-status", auth: true, intervalMs: BUSY_INTERVAL_MS },
  );
}

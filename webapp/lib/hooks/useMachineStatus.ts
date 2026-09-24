"use client";
// How full this machine is. A full box queues rather than refuses; occupancy counts the
// caller's own run too.

import { fetchMachineStatus, type MachineStatusResponse } from "@/lib/api";
import { useRead, type ReadResult } from "@/lib/hooks/useRead";

const BUSY_INTERVAL_MS = 5000;

export function useMachineStatus(): ReadResult<MachineStatusResponse> {
  return useRead(
    { key: "machine-status", fetch: fetchMachineStatus },
    { surface: "machine-status", auth: true, intervalMs: BUSY_INTERVAL_MS },
  );
}

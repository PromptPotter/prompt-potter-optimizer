"use client";

import { machineStatusRead, type MachineStatusResponse } from "@/lib/api";
import { useRead, type ReadResult } from "@/lib/hooks/useRead";

const BUSY_INTERVAL_MS = 5000;

export function useMachineStatus(): ReadResult<MachineStatusResponse> {
  return useRead(machineStatusRead(), { auth: true, intervalMs: BUSY_INTERVAL_MS });
}

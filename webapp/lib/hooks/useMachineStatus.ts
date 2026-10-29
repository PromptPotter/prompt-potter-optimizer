"use client";

import { machineStatusRead, type MachineStatusResponse } from "@/lib/api";
import { MACHINE_STATUS_POLL_MS } from "@/lib/cadence";
import { useRead, type ReadResult } from "@/lib/hooks/useRead";

export function useMachineStatus(): ReadResult<MachineStatusResponse> {
  return useRead(machineStatusRead(), { auth: true, intervalMs: MACHINE_STATUS_POLL_MS });
}

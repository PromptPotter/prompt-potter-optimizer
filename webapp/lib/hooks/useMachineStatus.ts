import { machineStatusRead } from "@/lib/api";
import type { MachineStatusResponse } from "@/lib/api/types";
import { useRead } from "@/lib/hooks/useRead";
import type { ReadResult } from "@/lib/read-cache";

export function useMachineStatus(): ReadResult<MachineStatusResponse> {
  return useRead(machineStatusRead());
}

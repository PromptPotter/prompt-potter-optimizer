"use client";

import { useMemo } from "react";
import { dashboardRead, isWarming } from "@/lib/api";
import type { MeasuredUnit, VerifyPassProgress, VerifyReading } from "@/lib/api/types";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { readyData, useRead } from "./useRead";

export interface VerifyState {
  readings: ReadonlyMap<string, VerifyReading>;
  pass: VerifyPassProgress | null;
  unit: MeasuredUnit | null;
}

export function useVerify(
  path: CyclePath | null,
  // Set only while a pass is awaited: nothing else moves the off-stream read.
  intervalMs?: number,
): VerifyState {
  const { dash } = useCycleStream();
  const { viewedPath } = useWorkspace();
  const key = path ? encodeCyclePath(path) : null;
  const streamed = key !== null && viewedPath != null && encodeCyclePath(viewedPath) === key;
  const read = readyData(
    useRead(path && !streamed ? dashboardRead(path, null) : null, { auth: true, intervalMs }),
  );
  const body = streamed ? dash : read && !isWarming(read) ? read : null;
  const rounds = body?.rounds;
  const pass = body?.verify_pass ?? null;
  const unit = body?.measured_unit ?? null;
  return useMemo(() => {
    const readings = new Map<string, VerifyReading>();
    for (const r of rounds ?? []) {
      for (const { reading } of r.candidates) {
        if (reading.verify) readings.set(reading.arm.label, reading.verify);
      }
    }
    return { readings, pass, unit };
  }, [rounds, pass, unit]);
}

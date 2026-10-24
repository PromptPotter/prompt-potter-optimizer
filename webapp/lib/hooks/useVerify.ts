"use client";

import { useMemo } from "react";
import { dashboardRead, isWarming } from "@/lib/api";
import type { MeasuredUnit, VerificationMark, VerifyReading } from "@/lib/api/types";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { readyData, useRead } from "./useRead";

export interface VerifyState {
  readings: ReadonlyMap<string, VerifyReading>;
  // Each arm's latest verify pass on the cycle, by `arm.key`.
  passes: ReadonlyMap<string, VerificationMark>;
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
  const marks = body?.verification;
  const unit = body?.measured_unit ?? null;
  return useMemo(() => {
    const readings = new Map<string, VerifyReading>();
    for (const r of rounds ?? []) {
      for (const { reading } of r.candidates) {
        if (reading.verify) readings.set(reading.arm.key, reading.verify);
      }
    }
    const passes = new Map(
      (marks ?? []).filter((m) => m.kind === "verify").map((m) => [m.arm.key, m]),
    );
    return { readings, passes, unit };
  }, [rounds, marks, unit]);
}

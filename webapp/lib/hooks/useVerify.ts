"use client";
// One cycle's verify state off its served dashboard: each candidate's last reading and the pass in
// flight. The viewed leaf rides the stream the chart reads, so the two cannot disagree; any other
// address is one read of the same route.

import { useMemo } from "react";
import { fetchDashboardByPath } from "@/lib/api";
import type {
  LiveDashboardState,
  MeasuredUnit,
  VerifyPassProgress,
  VerifyReading,
} from "@/lib/api/types";
import { verifyByLabel } from "@/lib/derivations";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import { useCycleStream } from "@/lib/poll";
import { useRevalidation } from "@/lib/revalidate";
import { useWorkspace } from "@/lib/workspace";
import { readyData, useRead } from "./useRead";

export interface VerifyState {
  readings: ReadonlyMap<string, VerifyReading>;
  pass: VerifyPassProgress | null;
  unit: MeasuredUnit;
}

// A cycle with no dashboard yet answers `{warming_up: true}`, which carries no rounds.
function asDashboard(body: Record<string, unknown> | null): LiveDashboardState | null {
  return body && Array.isArray(body.rounds) ? (body as unknown as LiveDashboardState) : null;
}

export function useVerify(
  path: CyclePath | null,
  // Set only while a pass is awaited: nothing else moves the off-stream read.
  intervalMs?: number,
): VerifyState {
  const { dash } = useCycleStream();
  const { viewedPath } = useWorkspace();
  const revalidateOn = useRevalidation();
  const key = path ? encodeCyclePath(path) : null;
  const streamed = key !== null && viewedPath != null && encodeCyclePath(viewedPath) === key;
  const read = readyData(
    useRead(
      path && key && !streamed
        ? {
            key,
            conditional: (signal, validator) => fetchDashboardByPath(path, validator, signal),
          }
        : null,
      { surface: "verify-dashboard", auth: true, intervalMs, revalidateOn },
    ),
  );
  const body = streamed ? dash : asDashboard(read);
  const rounds = body?.rounds;
  const pass = body?.verify_pass ?? null;
  const unit = body?.measured_unit ?? "sample";
  return useMemo(
    () => ({ readings: verifyByLabel(rounds ?? []), pass, unit }),
    [rounds, pass, unit],
  );
}

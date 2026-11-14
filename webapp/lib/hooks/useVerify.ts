import { useMemo } from "react";
import type { MeasuredUnit, VerificationMark, VerifyReading } from "@/lib/api/types";
import type { CyclePath } from "@/lib/ids";
import { useDashboard } from "@/lib/state/poll";

interface VerifyState {
  readings: ReadonlyMap<string, VerifyReading>;
  // Each arm's latest verify pass on the cycle, by `arm.key`.
  passes: ReadonlyMap<string, VerificationMark>;
  unit: MeasuredUnit | null;
}

export function useVerify(
  path: CyclePath | null,
  // Set only while a pass is awaited: nothing else moves another cycle's dashboard read.
): VerifyState {
  const { dash } = useDashboard(path);
  const rounds = dash?.rounds;
  const marks = dash?.verification;
  const unit = dash?.measured_unit ?? null;
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

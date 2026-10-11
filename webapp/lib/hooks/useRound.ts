"use client";

import { useMemo } from "react";
import { roundRead } from "../api";
import { groupByRound, roundCandidates, samplesForRow } from "../derivations";
import type { CyclePath } from "../ids";
import { roundOf, useDashboardAt, type DashboardSnapshot } from "../poll";
import type { ArmRow, RoundResult, SampleRow } from "../types";
import { useMomentAt } from "../workspace";
import { readyData, useRead, type ReadFailure } from "./useRead";

export interface RoundState {
  unfiled: boolean;
  doc: RoundResult | null;
  loading: boolean;
  failure: ReadFailure | null;
  rows: ArmRow[];
  row: (label: string) => ArmRow | null;
  samples: (row: ArmRow | null) => SampleRow[];
}

// Not `round_axis.completed`: it drops the empty rounds that closed before measuring yet have a file.
export function isRoundUnfiled(dash: DashboardSnapshot | null, round: number | null): boolean {
  const filed = (dash?.rounds ?? []).some((r) => r.round === round);
  return round != null && round === roundOf(dash) && !filed;
}

const NO_ROWS: ArmRow[] = [];

export function useRound(path: CyclePath | null, round: number | null): RoundState {
  const dash = useDashboardAt(path);
  const unfiled = isRoundUnfiled(dash, round);
  const at = useMomentAt(path);
  const read = useRead(path && round != null && !unfiled ? roundRead(path, round, at) : null);
  const doc = readyData(read);
  const loading = read.status === "loading";
  const failure = read.status === "failed" ? read.failure : null;

  const rows = useMemo(() => {
    if (round == null) return NO_ROWS;
    return groupByRound(roundCandidates(dash)).get(round) ?? NO_ROWS;
  }, [dash, round]);

  return useMemo(
    () => ({
      unfiled,
      doc,
      loading,
      failure,
      rows,
      row: (label) => rows.find((r) => r.reading.arm.label === label) ?? null,
      samples: (row) => (row ? samplesForRow(row, dash, doc) : []),
    }),
    [unfiled, doc, loading, failure, rows, dash],
  );
}

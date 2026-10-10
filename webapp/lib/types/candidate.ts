import type {
  ArmAbility,
  ArmNode,
  ArmReading,
  BenchPassProgress,
  LineageNode,
} from "@/lib/api/types";

export type CandidateSource = "history" | "inflight";

export type ThetaCaveat = NonNullable<ArmAbility["caveat"]>;

export interface ArmRow {
  // `R{round}.{idx}` — a row key, never a join key: joins ride `reading.arm`.
  key: string;
  source: CandidateSource;
  reading: ArmReading;
}

export interface CandidateBar {
  // `nodeKeyOf(node)`
  key: string;
  idx: number;
  label: string;
  node: LineageNode;
  // Null on a course bar, as is `reading`.
  arm: ArmNode | null;
  reading: ArmReading | null;
  source: CandidateSource;
  // An arm's `reading.own`, a course's served `run_standing`; `null` renders BLANK, never 0.
  level: number | null;
  // Null unless this bar answered the WHOLE shared basis.
  overlap: { rate: number | null; n: number } | null;
  // served: `dash.bench_pass`; a finished pass is `reading.bench`.
  benchPass: BenchPassProgress | null;
}

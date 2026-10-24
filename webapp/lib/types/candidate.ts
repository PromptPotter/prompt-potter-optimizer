import type {
  ArmAbility,
  ArmNode,
  ArmReading,
  CourseNode,
  VerificationMark,
} from "@/lib/api/types";

export type CandidateSource = "history" | "inflight";

export type ThetaCaveat = NonNullable<ArmAbility["caveat"]>;

export interface ArmRow {
  // `R{round}.{idx}` — a row key, never a join key: joins ride `reading.arm.key`.
  key: string;
  // The round whose panel holds the row; `reading.arm.round` is where the arm was first walked.
  round: number;
  source: CandidateSource;
  reading: ArmReading;
}

interface BarBase {
  // `nodeKeyOf(node)`
  key: string;
  idx: number;
  label: string;
}

// One bar of the candidates chart. `node` is the served tree row and the ONE reference to it;
// everything an arm measured or decided is read off `reading`, the half chosen for this row.
export interface ArmBar extends BarBase {
  node: ArmNode;
  // `node.reading` once its walk is decided, else the live row — WHOLE, never field by field.
  reading: ArmReading;
  source: CandidateSource;
  // Null unless this bar answered the WHOLE shared basis.
  overlap: { rate: number | null; n: number } | null;
  // served: `dash.verification`, this cycle's own passes on the arm.
  verify: VerificationMark | null;
  bench: VerificationMark | null;
}

// A run drawn as a bar: no arm, so no reading — its level is the served `run_standing`.
export interface CourseBar extends BarBase {
  node: CourseNode;
  reading: null;
}

export type CandidateBar = ArmBar | CourseBar;

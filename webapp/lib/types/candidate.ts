import type {
  ArmAbility,
  ArmReading,
  HierarchyCandidate,
  HierarchyCycle,
  VerificationMark,
} from "@/lib/api/types";

type CandidateSource = "history" | "inflight";

export type ThetaCaveat = NonNullable<ArmAbility["caveat"]>;

interface BarBase {
  // `node.address`
  key: string;
  idx: number;
  label: string;
}

// One bar of the candidates chart. `node` is the served tree row and the ONE reference to it;
// everything an arm measured or decided is read off `reading`, the half chosen for this row.
export interface ArmBar extends BarBase {
  node: HierarchyCandidate;
  // `node.facts.reading` once its walk is decided, else the live row — WHOLE, never field by field.
  reading: ArmReading;
  source: CandidateSource;
  // Null unless this bar answered the WHOLE shared basis.
  overlap: { rate: number | null; n: number } | null;
  // served: `dash.verification`, this cycle's own passes on the arm.
  verify: VerificationMark | null;
  bench: VerificationMark | null;
}

// A run drawn as a bar: no arm, so no reading — its level is its cycle's served `facts.standing`.
interface CycleBar extends BarBase {
  node: HierarchyCycle;
  reading: null;
}

export type CandidateBar = ArmBar | CycleBar;

import {
  type HierarchyCandidate,
  type HierarchyCycle,
  type LineArm,
  type LineageNode,
  type SubtreeCycle,
  type VerificationMark,
  DISPLAY_METRIC_INFO,
  type DisplayMetric,
} from "@/lib/api/types";
import type { CandidateBar } from "@/lib/types";
import { fmtUnit } from "@/lib/format";
import { tagValue } from "./hierarchy";
import { metricLevel } from "./headline-stats";
import { readPaired } from "./paired-reading";
import { candidateAt, childNodes, splitRetired, type LineageIndex } from "./lineage-candidates";

// A run's served standing rate against its origin; `null` renders BLANK, never 0.
function cycleLevel(node: HierarchyCycle): number | null {
  const served = node.facts.standing;
  const standing = served ? readPaired(served.vs_origin) : null;
  return standing?.read ? standing.lift.rate_b : null;
}

// A cycle bar's standing rate is a fitness, so it is blank under the other metrics.
export function barLevel(metric: DisplayMetric, bar: CandidateBar): number | null {
  if (bar.reading) return metricLevel(metric, bar.reading);
  return metric === "fitness" ? cycleLevel(bar.node) : null;
}

export function barStarted(bar: CandidateBar): boolean {
  return barLevel("fitness", bar) != null;
}

export function barValueLabel(metric: DisplayMetric, bar: CandidateBar): string {
  return fmtUnit(DISPLAY_METRIC_INFO[metric].unit, barLevel(metric, bar));
}

export function barsAreCycles(viewedNode: LineageNode | undefined): boolean {
  return viewedNode?.entry_kind === "candidate" && childNodes(viewedNode).length > 0;
}

type Row = { node: SubtreeCycle; arm: null } | { node: HierarchyCandidate; arm: LineArm };

interface CandidateBarsInput {
  index: LineageIndex;
  viewedNode: LineageNode | undefined;
  // served: `dash.line`, the viewed cycle's line across its fork chain; a bar is a mask over it.
  line: readonly LineArm[];
  sampleSet: number[] | null;
  marks: readonly VerificationMark[];
  // Off, a held round reads as its leading arm, the one its election is read off; a round not yet
  // held shows every arm it races.
  every: boolean;
}

export function candidateBars({
  index,
  viewedNode,
  line,
  sampleSet,
  marks,
  every,
}: CandidateBarsInput): CandidateBar[] {
  const markOf = (kind: VerificationMark["kind"], key: string) =>
    marks.find((m) => m.kind === kind && m.arm.key === key) ?? null;
  // A cycle's bars are its served line, each arm keyed into the tree by address, then the inner
  // runs it holds unplaced; a candidate's are its inner cycles.
  const cycles = (viewedNode ? childNodes(viewedNode) : []).flatMap<Row>((node) =>
    node.entry_kind === "cycle" ? [{ node, arm: null }] : [],
  );
  const rows: Row[] =
    viewedNode?.entry_kind === "cycle"
      ? [
          ...line.flatMap<Row>((arm) => {
            const node = candidateAt(index, arm.address);
            return node ? [{ node, arm }] : [];
          }),
          ...cycles,
        ]
      : cycles;
  // Live side of each supersede cut only — retired tails double a round.
  const live = new Set<LineageNode>(splitRetired(rows.map((r) => r.node)).live);
  const shown = rows.filter(
    ({ node, arm }) =>
      live.has(node) &&
      (every || !arm || !arm.reading.election.held || arm.reading.election.leading),
  );
  return shown.map<CandidateBar>(({ node, arm }, idx) => {
    const key = node.address;
    if (arm === null) {
      return {
        key,
        idx,
        label: node.facts.task ? node.label : (tagValue(node, "dataset") ?? node.label),
        node,
        reading: null,
      };
    }
    const { reading } = arm;
    // Both are served null short of the WHOLE basis: a rate over a shorter denominator sat another exam.
    const onLine = reading.on_origin_panel;
    const { sample_set_fitness: rate, sample_set_n: n } = node.facts;
    const overlap =
      sampleSet != null
        ? rate != null && n != null
          ? { rate, n }
          : null
        : onLine && { rate: onLine.rate, n: onLine.n };
    return {
      key,
      idx,
      label: node.label,
      node,
      reading,
      openCells: arm.open_cells,
      overlap,
      verify: markOf("verify", reading.arm.key),
      bench: markOf("bench", reading.arm.key),
    };
  });
}

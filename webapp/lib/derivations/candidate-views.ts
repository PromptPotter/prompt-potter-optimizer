import type {
  ArmReading,
  HierarchyCourse,
  LineageNode,
  VerificationMark,
} from "@/lib/api/types";
import { DISPLAY_METRIC_UNITS } from "@/lib/api/types.generated";
import type { CandidateBar } from "@/lib/types";
import { fmtLevel } from "@/lib/format";
import { tagValue } from "./hierarchy";
import { metricLevel, type DisplayMetric } from "./headline-stats";
import { panelCellLabel } from "./inner-panel";
import { readPaired } from "./paired-reading";
import { childNodes, splitRetired } from "./lineage-candidates";

// A run's served standing rate against its origin; `null` renders BLANK, never 0.
function courseLevel(node: HierarchyCourse): number | null {
  const served = node.facts.standing;
  const standing = served ? readPaired(served.vs_origin) : null;
  return standing?.read ? standing.lift.rate_b : null;
}

// A course bar's standing rate is an accuracy, so it is blank under the other metrics.
export function barLevel(metric: DisplayMetric, bar: CandidateBar): number | null {
  if (bar.reading) return metricLevel(metric, bar.reading);
  return metric === "accuracy" ? courseLevel(bar.node) : null;
}

export function barStarted(bar: CandidateBar): boolean {
  return barLevel("accuracy", bar) != null;
}

export function barValueLabel(metric: DisplayMetric, bar: CandidateBar): string {
  return fmtLevel(DISPLAY_METRIC_UNITS[metric], barLevel(metric, bar));
}

export function barsAreCourses(viewedNode: LineageNode | undefined): boolean {
  return viewedNode?.level === "candidate" && childNodes(viewedNode).length > 0;
}

export interface CandidateBarsInput {
  viewedNode: LineageNode | undefined;
  inflightByKey: ReadonlyMap<string, ArmReading>;
  sampleSet: number[] | null;
  marks: readonly VerificationMark[];
}

export function candidateBars({
  viewedNode,
  inflightByKey,
  sampleSet,
  marks,
}: CandidateBarsInput): CandidateBar[] {
  const markOf = (kind: VerificationMark["kind"], key: string) =>
    marks.find((m) => m.kind === kind && m.arm.key === key) ?? null;
  // Live side of each supersede cut only — retired tails double a round.
  const nodes: readonly LineageNode[] = viewedNode ? childNodes(viewedNode) : [];
  return splitRetired(nodes).live.map<CandidateBar>((node, idx) => {
    const key = node.address;
    if (node.level === "course") {
      return {
        key,
        idx,
        label: node.facts.task
          ? panelCellLabel(node.facts.task)
          : (tagValue(node, "dataset") ?? node.label),
        node,
        reading: null,
      };
    }
    const { reading } = node.facts;
    // ONE half per bar: the subtree serves `proposed`/`measuring` until the arm's walk is decided on the ledger.
    const decided = reading.state === "cut" || reading.state === "measured";
    const live = decided ? undefined : inflightByKey.get(reading.arm.key);
    // Both are served null short of the WHOLE basis: a rate over a shorter denominator sat another exam.
    const onLine = reading.on_origin_panel;
    const { sample_set_accuracy: rate, sample_set_n: n } = node.facts;
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
      reading: live ?? reading,
      source: live ? "inflight" : "history",
      overlap,
      verify: markOf("verify", reading.arm.key),
      bench: markOf("bench", reading.arm.key),
    };
  });
}

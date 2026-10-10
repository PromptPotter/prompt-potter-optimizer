import type { ArmReading, BenchPassProgress, LineageNode } from "@/lib/api/types";
import type { CandidateBar } from "@/lib/types";
import { fmtDisplayValue, metricLevel, type DisplayMetric } from "./headline-stats";
import { panelCellLabel } from "./inner-panel";
import { readPaired } from "./paired-reading";
import { nodeKeyOf, splitRetired } from "./lineage-candidates";

// A course bar's served standing rate is an accuracy, so it is blank under the other metrics.
export function barLevel(metric: DisplayMetric, bar: CandidateBar): number | null {
  if (bar.reading) return metricLevel(metric, bar.reading);
  return metric === "accuracy" ? bar.level : null;
}

export function barValueLabel(metric: DisplayMetric, bar: CandidateBar): string {
  return fmtDisplayValue(metric, barLevel(metric, bar));
}

export function barsAreCourses(viewedNode: LineageNode | undefined): boolean {
  return viewedNode?.kind === "candidate" && viewedNode.children.length > 0;
}

export interface CandidateBarsInput {
  viewedNode: LineageNode | undefined;
  inflightByLabel: ReadonlyMap<string, ArmReading>;
  sampleSet: number[] | null;
  benchPass: BenchPassProgress | null;
}

export function candidateBars({
  viewedNode,
  inflightByLabel,
  sampleSet,
  benchPass,
}: CandidateBarsInput): CandidateBar[] {
  // Live side of each supersede cut only — retired tails double a round.
  const nodes: readonly LineageNode[] = viewedNode?.children ?? [];
  return splitRetired(nodes).live.map<CandidateBar>((node, idx) => {
    const key = nodeKeyOf(node);
    if (node.kind === "course") {
      const standing = node.run_standing ? readPaired(node.run_standing.vs_origin) : null;
      return {
        key,
        idx,
        label: node.task ? panelCellLabel(node.task) : node.dataset_name,
        node,
        arm: null,
        reading: null,
        source: "history",
        level: standing?.read ? standing.lift.rate_b : null,
        overlap: null,
        benchPass: null,
      };
    }
    // ONE half per bar: the tree serves `outcome: null` until the arm's walk is decided on the ledger.
    const live =
      node.reading.outcome === null ? inflightByLabel.get(node.reading.arm.label) : undefined;
    const reading = live ?? node.reading;
    // Both are served null short of the WHOLE basis: a rate over a shorter denominator sat another exam.
    const onLine = node.reading.on_origin_panel;
    const overlap =
      sampleSet != null
        ? node.sample_set_accuracy != null && node.sample_set_n != null
          ? { rate: node.sample_set_accuracy, n: node.sample_set_n }
          : null
        : onLine && { rate: onLine.rate, n: onLine.n };
    return {
      key,
      idx,
      label: node.label,
      node,
      arm: node,
      reading,
      source: live ? "inflight" : "history",
      level: reading.own?.accuracy?.value ?? null,
      overlap,
      benchPass: benchPass?.label === node.reading.arm.label ? benchPass : null,
    };
  });
}

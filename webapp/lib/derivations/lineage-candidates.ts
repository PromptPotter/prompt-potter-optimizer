import type {
  GeneSource,
  HierarchyCandidate,
  HierarchyCycle,
  LineageNode,
  SubtreeCampaign,
  SubtreeCycle,
  SubtreeEntry,
  SubjectReading,
  DisplayMetric,
} from "@/lib/api/types";
import { readingPath } from "@/lib/api/reads";
import { selectedCandidateOf, type SelectedCandidate } from "@/lib/types";
import { pickedAddressOf, type CandidatePick } from "@/lib/address";
import { tagValue } from "./hierarchy";
import { cycleAddressOf, encodeCyclePath, type CyclePath } from "@/lib/ids";
import { metricLevel } from "./headline-stats";

export interface RetiredGroup {
  // The address of the cycle that took the tail's place.
  branch: string;
  candidates: HierarchyCandidate[];
}

// Grouped by BRANCH: two cuts retire two different tails.
export function splitRetired<N extends LineageNode>(
  rows: readonly N[],
): { live: N[]; retired: RetiredGroup[] } {
  const byBranch = new Map<string, HierarchyCandidate[]>();
  const live: N[] = [];
  for (const node of rows) {
    const branch = node.entry_kind === "candidate" ? tagValue(node, "superseded") : null;
    if (node.entry_kind === "candidate" && branch) {
      byBranch.set(branch, [...(byBranch.get(branch) ?? []), node]);
    } else live.push(node);
  }
  return { live, retired: [...byBranch].map(([branch, candidates]) => ({ branch, candidates })) };
}

// A cycle's own candidates, round by round in slot order, then the inner cycles it holds
// unplaced; a candidate's inner cycles.
export function childNodes(node: LineageNode): LineageNode[] {
  if (node.entry_kind === "cycle") return node.children.flatMap<LineageNode>((c) => c.children);
  return node.children.flatMap((c) => c.children);
}

// A candidate entry as the workspace's pick (`workspace.tsx::pickCandidate`).
export function pickOf(node: HierarchyCandidate): CandidatePick {
  const { round, key } = node.facts.reading.arm;
  return { round, arm: key };
}

export function selectedNodeOf(node: HierarchyCandidate, cycleId: string): SelectedCandidate {
  const { round, key, label } = node.facts.reading.arm;
  return selectedCandidateOf(cycleId, round, key, label);
}

export interface LineageIndex {
  byAddress: ReadonlyMap<string, SubtreeCampaign | SubtreeCycle | HierarchyCandidate>;
  // Every cycle at any depth, a campaign's root first.
  cycles: readonly SubtreeCycle[];
  candidates: readonly HierarchyCandidate[];
}

export const EMPTY_LINEAGE: LineageIndex = { byAddress: new Map(), cycles: [], candidates: [] };

export function indexLineage(root: SubtreeCampaign | null): LineageIndex {
  if (!root) return EMPTY_LINEAGE;
  const byAddress = new Map<string, SubtreeCampaign | SubtreeCycle | HierarchyCandidate>();
  const cycles: SubtreeCycle[] = [];
  const candidates: HierarchyCandidate[] = [];
  const visit = (entry: SubtreeEntry): void => {
    if (entry.entry_kind !== "round") byAddress.set(entry.address, entry);
    if (entry.entry_kind === "cycle") cycles.push(entry);
    if (entry.entry_kind === "candidate") candidates.push(entry);
    entry.children.forEach(visit);
  };
  visit(root);
  return { byAddress, cycles, candidates };
}

export function cycleAt(index: LineageIndex, path: CyclePath | null): SubtreeCycle | null {
  const entry = path ? index.byAddress.get(encodeCyclePath(path)) : undefined;
  return entry?.entry_kind === "cycle" ? entry : null;
}

function cycleOf(index: LineageIndex, node: LineageNode): SubtreeCycle | null {
  if (node.entry_kind === "cycle") return node;
  const entry = index.byAddress.get(cycleAddressOf(node.address));
  return entry?.entry_kind === "cycle" ? entry : null;
}

function candidateAt(index: LineageIndex, address: string | null): HierarchyCandidate | null {
  const entry = address ? index.byAddress.get(address) : undefined;
  return entry?.entry_kind === "candidate" ? entry : null;
}

// The address of the candidate a reading resolved to: its cycle, its round, its arm.
export function readingAddress(reading: SubjectReading): string {
  const cycle = encodeCyclePath(readingPath(reading));
  return pickedAddressOf(cycle, { round: reading.round, arm: reading.candidate_id });
}

// A channel's arms on its reading's cycle: its own, the one standing, and the newest; `seed` is
// what its pick reads until picked.
export function channelArms(index: LineageIndex, reading: SubjectReading | null, headline: boolean) {
  const candidates = candidatesAtPath(index, reading && readingPath(reading));
  const own = reading ? candidateAt(index, readingAddress(reading)) : null;
  const winner = candidates.find((c) => c.facts.stands) ?? own;
  const latest = candidates.find((c) => c.facts.cycle_latest) ?? null;
  return { own, winner, latest, seed: headline ? winner : own };
}

export function candidatesAtPath(index: LineageIndex, path: CyclePath | null): HierarchyCandidate[] {
  const cycle = cycleAt(index, path);
  return cycle ? candidatesOf(cycle) : [];
}

// A cycle's candidates, round by round in slot order; not the inner campaigns it holds unplaced.
export function candidatesOf(cycle: SubtreeCycle): HierarchyCandidate[] {
  return cycle.children.flatMap((c) => (c.entry_kind === "round" ? c.children : []));
}

// By its hierarchy address: one arm re-measured in two rounds is two candidates.
export function armAt(
  index: LineageIndex,
  path: CyclePath | null,
  pick: CandidatePick,
): HierarchyCandidate | null {
  const entry = path && index.byAddress.get(pickedAddressOf(encodeCyclePath(path), pick));
  return entry?.entry_kind === "candidate" ? entry : null;
}

// Every candidate *edited* holds and each descending from one along the served descent
// (`descends_from`, `donor`), by address: an edit withdraws every measurement under it.
export function withdrawnUnder(
  index: LineageIndex,
  edited: (c: HierarchyCandidate) => boolean,
): ReadonlySet<string> {
  const settled = new Map<string, boolean>();
  const walk = (address: string | null): boolean => {
    const c = candidateAt(index, address);
    if (c === null) return false;
    const held = settled.get(c.address);
    if (held !== undefined) return held;
    // Settled false first, so a cycle in the served edges terminates instead of hanging the tab.
    settled.set(c.address, false);
    const out = edited(c) || walk(c.facts.descends_from) || walk(c.facts.donor);
    settled.set(c.address, out);
    return out;
  };
  return new Set(index.candidates.filter((c) => walk(c.address)).map((c) => c.address));
}

export type LineStep = { kind: "step"; node: HierarchyCandidate } | { kind: "held"; round: number };

export function mainLineOf(index: LineageIndex, head: HierarchyCandidate): LineStep[] {
  return head.facts.main_line.flatMap<LineStep>((step) => {
    if (step.candidates.length === 0) return [{ kind: "held", round: step.round }];
    return step.candidates.flatMap<LineStep>((address) => {
      const node = candidateAt(index, address);
      return node ? [{ kind: "step", node }] : [];
    });
  });
}

export interface Descent {
  // The address of its main, the candidate it descends from: the one edge every lineage drawing draws.
  parent: string | null;
  // The address of its declared donor, of any round, and the loci it gave.
  donor: string | null;
  donated: string[];
  // Per locus no declared parent explains, the served source ranked first.
  genes: GeneSource[];
}

const NO_DESCENT: Descent = { parent: null, donor: null, donated: [], genes: [] };

export function descentOf(node: HierarchyCycle | HierarchyCandidate): Descent {
  if (node.entry_kind !== "candidate") return NO_DESCENT;
  const genes: GeneSource[] = [];
  for (const gene of node.facts.gene_sources) {
    if (!genes.some((g) => g.locus === gene.locus)) genes.push(gene);
  }
  const { descends_from, donor, donated_loci } = node.facts;
  return { parent: descends_from, donor, donated: donated_loci, genes };
}

// As the cycle names it (`facts.origin`): never a walk up `parent_ids`, never a round-0 label.
export function originOf(index: LineageIndex, node: LineageNode | null): HierarchyCandidate | null {
  const cycle = node ? cycleOf(index, node) : null;
  return candidateAt(index, cycle?.facts.origin ?? null);
}

export function originAt(index: LineageIndex, path: CyclePath | null): HierarchyCandidate | null {
  return originOf(index, cycleAt(index, path));
}

interface NodeOverlays {
  valueByKey: ReadonlyMap<string, number | null>;
  thetaByKey: ReadonlyMap<string, number | null>;
}

export function nodeOverlays(index: LineageIndex, metric: DisplayMetric): NodeOverlays {
  const valueByKey = new Map<string, number | null>();
  const thetaByKey = new Map<string, number | null>();
  for (const arm of index.candidates) {
    valueByKey.set(arm.address, metricLevel(metric, arm.facts.reading));
    thetaByKey.set(arm.address, metricLevel("ability", arm.facts.reading));
  }
  return { valueByKey, thetaByKey };
}

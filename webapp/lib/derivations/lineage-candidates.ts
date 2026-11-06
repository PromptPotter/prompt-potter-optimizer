import type {
  GeneSource,
  HierarchyCandidate,
  HierarchyCourse,
  LineageNode,
  SubjectReading,
  SubtreeCampaign,
  SubtreeCourse,
  SubtreeEntry,
  DisplayMetric,
} from "@/lib/api/types";
import { selectedCandidateOf, type SelectedCandidate } from "@/lib/types";
import { tagValue, type RootGroup } from "./hierarchy";
import { courseAddressOf, encodeCyclePath, type CyclePath } from "@/lib/ids";
import { metricLevel } from "./headline-stats";

export interface RetiredGroup {
  // The address of the course that took the tail's place.
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
    const branch = node.level === "candidate" ? tagValue(node, "superseded") : null;
    if (node.level === "candidate" && branch) {
      byBranch.set(branch, [...(byBranch.get(branch) ?? []), node]);
    } else live.push(node);
  }
  return { live, retired: [...byBranch].map(([branch, candidates]) => ({ branch, candidates })) };
}

// A course's own candidates, round by round in slot order, then the inner courses it holds
// unplaced; a candidate's inner courses.
export function childNodes(node: LineageNode): LineageNode[] {
  if (node.level === "course") return node.children.flatMap<LineageNode>((c) => c.children);
  return node.children.flatMap((c) => c.children);
}

export function selectedNodeOf(node: HierarchyCandidate, cycleId: string): SelectedCandidate {
  const { round, key, label } = node.facts.reading.arm;
  return selectedCandidateOf(cycleId, round, key, label);
}

export interface LineageIndex {
  byAddress: ReadonlyMap<string, SubtreeCampaign | SubtreeCourse | HierarchyCandidate>;
  // Every course at any depth, a campaign's root first.
  courses: readonly SubtreeCourse[];
  candidates: readonly HierarchyCandidate[];
}

export const EMPTY_LINEAGE: LineageIndex = { byAddress: new Map(), courses: [], candidates: [] };

export function indexLineage(root: SubtreeCampaign | null): LineageIndex {
  if (!root) return EMPTY_LINEAGE;
  const byAddress = new Map<string, SubtreeCampaign | SubtreeCourse | HierarchyCandidate>();
  const courses: SubtreeCourse[] = [];
  const candidates: HierarchyCandidate[] = [];
  const visit = (entry: SubtreeEntry): void => {
    if (entry.level !== "round") byAddress.set(entry.address, entry);
    if (entry.level === "course") courses.push(entry);
    if (entry.level === "candidate") candidates.push(entry);
    entry.children.forEach(visit);
  };
  visit(root);
  return { byAddress, courses, candidates };
}

export function courseAt(index: LineageIndex, path: CyclePath | null): SubtreeCourse | null {
  const entry = path ? index.byAddress.get(encodeCyclePath(path)) : undefined;
  return entry?.level === "course" ? entry : null;
}

export function courseOf(index: LineageIndex, node: LineageNode): SubtreeCourse | null {
  if (node.level === "course") return node;
  const entry = index.byAddress.get(courseAddressOf(node.address));
  return entry?.level === "course" ? entry : null;
}

// A course's campaign is its served `parent`.
export function campaignOf(index: LineageIndex, course: SubtreeCourse | null): SubtreeCampaign | null {
  const entry = course?.parent ? index.byAddress.get(course.parent) : undefined;
  return entry?.level === "campaign" ? entry : null;
}

// The dataset the course at *path* measures: its campaign's served `dataset` tag.
export function datasetAt(index: LineageIndex, path: CyclePath | null): string | null {
  return tagValue(campaignOf(index, courseAt(index, path)), "dataset");
}

// The course a fork or diag at *path* was cut from, off its served spawn; null on a root.
export function spawnParentAt(index: LineageIndex, path: CyclePath | null): SubtreeCourse | null {
  const spawn = courseAt(index, path)?.facts.spawn;
  const entry = spawn ? index.byAddress.get(spawn.parent) : undefined;
  return entry?.level === "course" ? entry : null;
}

// The short label a ranked edit's campaign is served under: every edit ranks inside a campaign
// one of the read's subjects reads, so a miss is a reading this read did not serve.
export function editCampaignName(
  subjects: readonly SubjectReading[],
  edit: { campaign_id: string },
): string | null {
  return subjects.find((s) => s.campaign_id === edit.campaign_id)?.short_labels.campaign ?? null;
}

// What a forest card names a course: a root by the dataset it measures, a sibling by its short label.
export function courseName(course: HierarchyCourse): string {
  return course.facts.mint === "root"
    ? (tagValue(course, "dataset") ?? course.short_label)
    : course.short_label;
}

// The origin course a group of campaigns shares: the first's served root. The listing is read at
// depth 1, so every started campaign holds its root course; a group of two holds no unstarted one.
export function groupOrigin(group: RootGroup): HierarchyCourse {
  const [first] = group.campaigns;
  const origin =
    first?.level === "campaign"
      ? first.children.find((course) => course.address === first.facts.root)
      : undefined;
  if (origin === undefined) throw new Error(`unlisted origin ${group.rootCycleId}`);
  return origin;
}

function candidateAt(index: LineageIndex, address: string | null): HierarchyCandidate | null {
  const entry = address ? index.byAddress.get(address) : undefined;
  return entry?.level === "candidate" ? entry : null;
}

export function candidatesAtPath(index: LineageIndex, path: CyclePath | null): HierarchyCandidate[] {
  const course = courseAt(index, path);
  return course ? candidatesOf(course) : [];
}

// A course's candidates, round by round in slot order; not the inner campaigns it holds unplaced.
export function candidatesOf(course: SubtreeCourse): HierarchyCandidate[] {
  return course.children.flatMap((c) => (c.level === "round" ? c.children : []));
}

function armOfId(index: LineageIndex, id: string): HierarchyCandidate | null {
  return index.candidates.find((c) => c.facts.individual === id && c.facts.answers_for_id) ?? null;
}

export function armAt(
  index: LineageIndex,
  path: CyclePath | null,
  key: string,
): HierarchyCandidate | null {
  return candidatesAtPath(index, path).find((c) => c.facts.reading.arm.key === key) ?? null;
}

// The candidate answering for the arm's individual; null for a refused proposal, which measured none.
export function individualOf(
  index: LineageIndex,
  node: HierarchyCandidate,
): HierarchyCandidate | null {
  return node.facts.reading.end === "invalid" ? null : armOfId(index, node.facts.individual);
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

export interface Spine {
  // The course of the spine's head and its campaign, as the tree serves them.
  course: SubtreeCourse;
  campaign: SubtreeCampaign;
  // The served main line to the head, origin first; a held round adds no point.
  chain: HierarchyCandidate[];
  // The subject labels marked on each individual of the chain.
  marked: Map<string, string[]>;
}

// One spine per main line the subjects read on: a subject on a prefix of a longer one is marked
// on it, never drawn twice.
export function spinesOf(index: LineageIndex, subjects: readonly SubjectReading[]): Spine[] {
  const nodes = new Map<string, HierarchyCandidate>();
  for (const cand of index.candidates) {
    if (cand.facts.answers_for_id) nodes.set(cand.facts.individual, cand);
  }
  const chains = new Map<string, { chain: HierarchyCandidate[]; marked: Map<string, string[]> }>();
  for (const s of subjects) {
    const head = nodes.get(s.candidate_id);
    if (!head) continue;
    const chain = mainLineOf(index, head).flatMap((step) => (step.kind === "step" ? [step.node] : []));
    const rootId = chain[0]?.facts.individual ?? s.candidate_id;
    const prior = chains.get(rootId);
    const merged = !prior || chain.length > prior.chain.length ? chain : prior.chain;
    const marked = prior?.marked ?? new Map<string, string[]>();
    marked.set(s.candidate_id, [...(marked.get(s.candidate_id) ?? []), s.label]);
    chains.set(rootId, { chain: merged, marked });
  }
  // A head the tree holds sits in a course of a campaign it holds: a miss draws no spine.
  return [...chains.values()].flatMap(({ chain, marked }) => {
    const head = chain.at(-1);
    const course = head ? courseOf(index, head) : null;
    const campaign = campaignOf(index, course);
    return course && campaign ? [{ course, campaign, chain, marked }] : [];
  });
}

interface Descent {
  // The address of its main, the candidate it descends from: the one edge every lineage drawing draws.
  parent: string | null;
  // The address of its declared donor, of any round, and the loci it gave.
  donor: string | null;
  donated: string[];
  // Per locus no declared parent explains, the served source ranked first.
  genes: GeneSource[];
}

const NO_DESCENT: Descent = { parent: null, donor: null, donated: [], genes: [] };

export function descentOf(node: HierarchyCourse | HierarchyCandidate): Descent {
  if (node.level !== "candidate") return NO_DESCENT;
  const genes: GeneSource[] = [];
  for (const gene of node.facts.gene_sources) {
    if (!genes.some((g) => g.locus === gene.locus)) genes.push(gene);
  }
  const { descends_from, donor, donated_loci } = node.facts;
  return { parent: descends_from, donor, donated: donated_loci, genes };
}

// As the course names it (`facts.origin`): never a walk up `parent_ids`, never a round-0 label.
export function originOf(index: LineageIndex, node: LineageNode | null): HierarchyCandidate | null {
  const course = node ? courseOf(index, node) : null;
  return candidateAt(index, course?.facts.origin ?? null);
}

export function originAt(index: LineageIndex, path: CyclePath | null): HierarchyCandidate | null {
  return originOf(index, courseAt(index, path));
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

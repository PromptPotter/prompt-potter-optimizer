import { describe, expect, it } from "vitest";
import type { HierarchyCampaign, HierarchyCandidate, SubjectReading } from "@/lib/api/types";
import {
  courseTags,
  hierarchyCandidate,
  roundsOf,
  subtreeCampaign,
  subtreeCourse,
} from "@/lib/test-fixtures";
import { decodeCyclePath } from "@/lib/ids";
import { groupByRoot } from "../hierarchy";
import {
  courseName,
  datasetAt,
  editCampaignName,
  groupOrigin,
  indexLineage,
  spawnParentAt,
  spinesOf,
} from "../lineage-candidates";

const CAMP = "squad__a1b2c3";
const ROOT = `${CAMP}::cycle_0123456789`;
const FORK = `${CAMP}::cycle_0123456789_fork_77aa`;
const INNER_CAMP = `${ROOT}~inner__d4e5f6`;
const INNER = `${INNER_CAMP}::cycle_inner`;

const path = (address: string) => decodeCyclePath(address);

// The ids decide nothing: every label below is the served one, which no id parse reproduces.
function tree(cands: { root?: HierarchyCandidate[]; fork?: HierarchyCandidate[] } = {}) {
  const inner = subtreeCampaign({
    address: INNER_CAMP,
    tags: [{ kind: "dataset", value: "inner-bank", label: "inner-bank" }],
    children: [subtreeCourse({ address: INNER, parent: INNER_CAMP }, { mint: "inner" })],
  });
  const origin = hierarchyCandidate(ROOT, { id: "C0" }, { children: [inner] });
  return indexLineage(
    subtreeCampaign({
      address: CAMP,
      short_label: "served-campaign",
      tags: [{ kind: "dataset", value: "squad", label: "squad" }],
      children: [
        subtreeCourse({
          address: ROOT,
          parent: CAMP,
          short_label: "served-root",
          tags: courseTags("root", "squad"),
          children: roundsOf(ROOT, [origin, ...(cands.root ?? [])]),
        }),
        subtreeCourse(
          {
            address: FORK,
            parent: CAMP,
            short_label: "served-fork",
            children: roundsOf(FORK, cands.fork ?? []),
          },
          {
            mint: "fork",
            spawn: {
              parent: ROOT,
              cut_round: 1,
              trigger: "operator_steered",
              direction: "offshoot",
              issued_by: "op",
            },
          },
        ),
      ],
    }),
  );
}

describe("datasetAt", () => {
  it("reads the viewed course's campaign tag, at the root and inside a sandbox", () => {
    const index = tree();
    expect(datasetAt(index, path(FORK))).toBe("squad");
    expect(datasetAt(index, path(INNER))).toBe("inner-bank");
  });

  it("answers null where the tree does not hold the path", () => {
    expect(datasetAt(tree(), path(`${CAMP}::cycle_gone`))).toBeNull();
    expect(datasetAt(tree(), null)).toBeNull();
  });
});

describe("spawnParentAt", () => {
  it("reads a fork's parent off its served spawn", () => {
    expect(spawnParentAt(tree(), path(FORK))?.short_label).toBe("served-root");
  });

  it("answers null on a root, which no spawn names", () => {
    expect(spawnParentAt(tree(), path(ROOT))).toBeNull();
  });
});

describe("editCampaignName", () => {
  // A sandboxed campaign is in no listing: only its reading serves the label.
  const sandboxed = {
    kind: "candidate",
    campaign_id: "inner__d4e5f6",
    inside: [{ campaign_id: CAMP, cycle_id: "cycle_0123456789" }],
    short_labels: { campaign: "served-inner", course: "served-course", seed_of: "served-outer" },
  } as SubjectReading;

  it("names a ranked edit's campaign by the label its subject's reading serves, sandboxed too", () => {
    expect(editCampaignName([sandboxed], { campaign_id: "inner__d4e5f6" })).toBe("served-inner");
  });

  it("answers null for a campaign none of the read's subjects reads", () => {
    expect(editCampaignName([sandboxed], { campaign_id: CAMP })).toBeNull();
  });
});

describe("courseName", () => {
  it("names a root by its dataset and a sibling by its served short label", () => {
    const index = tree();
    const root = index.courses.find((c) => c.address === ROOT);
    const fork = index.courses.find((c) => c.address === FORK);
    expect([root && courseName(root), fork && courseName(fork)]).toEqual(["squad", "served-fork"]);
  });
});

describe("groupOrigin", () => {
  const listed = (address: string, children: HierarchyCampaign["children"]): HierarchyCampaign => {
    const c = subtreeCampaign({ address, children: [] });
    return { ...c, children, facts: { ...c.facts, root: `${address}::cycle_shared` } };
  };
  const origin = subtreeCourse({ address: `a__1::cycle_shared`, short_label: "served-origin" });

  it("names a group by its first campaign's served root course", () => {
    const [group] = groupByRoot([listed("a__1", [origin]), listed("b__2", [])]);
    expect(group && groupOrigin(group).short_label).toBe("served-origin");
  });

  it("refuses a group whose first campaign holds no root course", () => {
    const [group] = groupByRoot([listed("a__1", []), listed("b__2", [])]);
    expect(() => group && groupOrigin(group)).toThrow(/unlisted origin/);
  });
});

describe("spinesOf", () => {
  // `parent_ids[0]` points at the losing arm: a walk up would draw the line through it.
  const origin = hierarchyCandidate(ROOT, { id: "C0" });
  const loser = hierarchyCandidate(ROOT, { id: "R1.1", round: 1, parent_ids: ["C0"] });
  const crowned = hierarchyCandidate(ROOT, { id: "R1.2", round: 1, parent_ids: ["C0"] });
  const head = hierarchyCandidate(FORK, { id: "F2.1", round: 2, parent_ids: ["R1.1"] });
  head.facts.main_line = [
    { round: 0, candidates: [origin.address] },
    { round: 1, candidates: [crowned.address] },
    { round: 2, candidates: [head.address] },
  ];
  crowned.facts.main_line = head.facts.main_line.slice(0, 2);
  const index = tree({ root: [loser, crowned], fork: [head] });
  const read = (id: string, label: string) => ({ candidate_id: id, label }) as SubjectReading;

  it("draws the served main line, never the parent walk", () => {
    const [spine] = spinesOf(index, [read("F2.1", "head")]);
    expect(spine?.chain.map((c) => c.facts.individual)).toEqual(["C0", "R1.2", "F2.1"]);
  });

  it("names the head's course and campaign by their served short labels", () => {
    const [spine] = spinesOf(index, [read("F2.1", "head")]);
    expect([spine?.campaign.short_label, spine?.course.short_label]).toEqual([
      "served-campaign",
      "served-fork",
    ]);
  });

  it("draws no spine for a head whose course the tree does not place", () => {
    const stray = hierarchyCandidate(`${CAMP}::cycle_gone`, { id: "G1.1", round: 1 });
    stray.facts.main_line = [{ round: 1, candidates: [stray.address] }];
    const placed = indexLineage(null);
    const withStray = { ...index, candidates: [...index.candidates, stray] };
    withStray.byAddress = new Map([...index.byAddress, [stray.address, stray]]);
    expect(spinesOf(placed, [read("G1.1", "stray")])).toEqual([]);
    expect(spinesOf(withStray, [read("G1.1", "stray")])).toEqual([]);
  });

  it("marks a subject on a prefix of a longer line on that one spine", () => {
    const spines = spinesOf(index, [read("R1.2", "mid"), read("F2.1", "head")]);
    expect(spines).toHaveLength(1);
    expect([...(spines[0]?.marked ?? [])]).toEqual([
      ["R1.2", ["mid"]],
      ["F2.1", ["head"]],
    ]);
  });
});

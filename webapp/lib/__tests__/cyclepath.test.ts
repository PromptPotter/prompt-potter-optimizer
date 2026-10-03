import { describe, it, expect } from "vitest";
import {
  decodeCyclePath,
  encodeCyclePath,
  encodeDescend,
  nodeAddress,
  ownerOfNodeAddress,
  pathLeaf,
  pathRoot,
  type CyclePath,
} from "@/lib/ids";

describe("CyclePath", () => {
  const outer = { campaignId: "justlogic__ab12cd", cycleId: "cycle_9f3a1b" };
  const inner = { campaignId: "justlogic__ff00aa", cycleId: "cycle_1122ab_s3" };
  const depth1: CyclePath = [outer];
  const depth2: CyclePath = [outer, inner];

  // The PROPERTY, not the literal: the separators are generated from `domain/cycle_paths.py`.
  it("round-trips a top-level (1-hop) path", () => {
    expect(decodeCyclePath(encodeCyclePath(depth1))).toEqual(depth1);
  });

  it("round-trips a deep (2-hop) path", () => {
    expect(decodeCyclePath(encodeCyclePath(depth2))).toEqual(depth2);
  });

  it("returns null on malformed input", () => {
    expect(decodeCyclePath("")).toBeNull();
    expect(decodeCyclePath("no-separator")).toBeNull();
    expect(decodeCyclePath("camp::cy~broken")).toBeNull();
    expect(decodeCyclePath("bad/slash::cy")).toBeNull();
    expect(decodeCyclePath("camp::cy space")).toBeNull();
    // All-dots components pass the id charset but are traversal segments the
    // server rejects (`store/io.py::validate_path_component`).
    expect(decodeCyclePath("..::..")).toBeNull();
  });

  it("reads root and leaf hops", () => {
    expect(pathRoot(depth2)).toBe(outer);
    expect(pathLeaf(depth2)).toBe(inner);
    expect(pathRoot(depth1)).toBe(outer);
    expect(pathLeaf(depth1)).toBe(outer);
  });

  it("encodes descend as empty at depth 1, the inner hops when deep", () => {
    expect(encodeDescend(depth1)).toBe("");
    expect(encodeDescend(depth2)).toBe("justlogic__ff00aa::cycle_1122ab_s3");
  });

  // A null owner silently makes `toggle` a no-op, so a suffixed address must still resolve.
  describe("node addresses", () => {
    it("owns a course address by its root-hop campaign, at any depth", () => {
      expect(ownerOfNodeAddress(nodeAddress(depth1))).toBe("justlogic__ab12cd");
      expect(ownerOfNodeAddress(nodeAddress(depth2))).toBe("justlogic__ab12cd");
    });

    it("owns a candidate address by its campaign, suffix and all", () => {
      const addr = nodeAddress(depth2, "0d3d5cd5adf74270b194d23cad09e022");
      expect(addr).toBe(
        "justlogic__ab12cd::cycle_9f3a1b~justlogic__ff00aa::cycle_1122ab_s3" +
          "|0d3d5cd5adf74270b194d23cad09e022",
      );
      expect(ownerOfNodeAddress(addr)).toBe("justlogic__ab12cd");
    });

    it("owns an origin address by the origin id — it names no campaign", () => {
      // An origin has no campaign; `cycle_<hash>` and `{dataset}__{rand6}` owners cannot collide.
      expect(ownerOfNodeAddress(nodeAddress([], "cycle_47d99f21ef84"))).toBe(
        "cycle_47d99f21ef84",
      );
    });

    it("answers null on a malformed address rather than a wrong owner", () => {
      expect(ownerOfNodeAddress("")).toBeNull();
      expect(ownerOfNodeAddress("no-separator|cand")).toBeNull();
      expect(ownerOfNodeAddress("|bad/slash")).toBeNull();
    });
  });
});

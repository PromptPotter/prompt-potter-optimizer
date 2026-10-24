import { describe, it, expect } from "vitest";
import {
  courseAddressOf,
  decodeCyclePath,
  encodeCyclePath,
  encodeDescend,
  ownerOfAddress,
  pathLeaf,
  pathOfAddress,
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
    // All-dots passes the id charset but is a traversal segment (`store/io.py::validate_path_component`).
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

  // A null owner silently makes `toggle` a no-op, so an address below a course must still resolve.
  describe("hierarchy addresses", () => {
    const course = encodeCyclePath(depth2);
    const candidate = `${course}/r3/0d3d5cd5adf74270b194d23cad09e022`;

    it("heads an address below a course with the course's own", () => {
      expect(courseAddressOf(candidate)).toBe(course);
      expect(courseAddressOf(course)).toBe(course);
      expect(pathOfAddress(candidate)).toEqual(depth2);
    });

    it("names no path for a campaign address", () => {
      expect(() => pathOfAddress("justlogic__ab12cd")).toThrow();
    });

    it("owns an address by its root-hop campaign, at any depth", () => {
      expect(ownerOfAddress(encodeCyclePath(depth1))).toBe("justlogic__ab12cd");
      expect(ownerOfAddress(candidate)).toBe("justlogic__ab12cd");
      expect(ownerOfAddress("justlogic__ab12cd")).toBe("justlogic__ab12cd");
    });

    it("owns an origin address by the origin id — it names no campaign", () => {
      // `cycle_<hash>` and `{dataset}__{rand6}` owners cannot collide.
      expect(ownerOfAddress("cycle_47d99f21ef84")).toBe("cycle_47d99f21ef84");
    });

    it("answers null on a malformed address rather than a wrong owner", () => {
      expect(ownerOfAddress("")).toBeNull();
      expect(ownerOfAddress("::cy")).toBeNull();
      expect(ownerOfAddress("bad space::cy/r1/k")).toBeNull();
    });
  });
});

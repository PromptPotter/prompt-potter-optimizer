import { describe, it, expect } from "vitest";
import {
  decodeCyclePath,
  encodeCyclePath,
  encodeDescend,
  pathLeaf,
  pathRoot,
  samePath,
  type CyclePath,
} from "@/lib/ids";

// CyclePath is the single viewed-cycle address (root → leaf hops). Encode/decode
// is URL glue + the `?descend=` wire param, so lock the round-trip, the malformed
// guard, and the root/leaf/descend derivations here.
describe("CyclePath", () => {
  const outer = { campaignId: "gsm8k__ab12cd", cycleId: "cycle_9f3a1b" };
  const inner = { campaignId: "justlogic__ff00aa", cycleId: "cycle_1122ab_s3" };
  const depth1: CyclePath = [outer];
  const depth2: CyclePath = [outer, inner];

  it("round-trips a top-level (1-hop) path", () => {
    const s = encodeCyclePath(depth1);
    expect(s).toBe("gsm8k__ab12cd::cycle_9f3a1b");
    expect(decodeCyclePath(s)).toEqual(depth1);
  });

  it("round-trips a deep (2-hop) path", () => {
    const s = encodeCyclePath(depth2);
    expect(s).toBe("gsm8k__ab12cd::cycle_9f3a1b~justlogic__ff00aa::cycle_1122ab_s3");
    expect(decodeCyclePath(s)).toEqual(depth2);
  });

  it("returns null on malformed input", () => {
    expect(decodeCyclePath("")).toBeNull();
    expect(decodeCyclePath("no-separator")).toBeNull();
    expect(decodeCyclePath("camp::cy~broken")).toBeNull();
    expect(decodeCyclePath("bad/slash::cy")).toBeNull();
    expect(decodeCyclePath("camp::cy space")).toBeNull();
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

  it("compares paths by value", () => {
    expect(samePath(depth2, [outer, inner])).toBe(true);
    expect(samePath(depth1, depth2)).toBe(false);
    expect(samePath(null, null)).toBe(true);
    expect(samePath(depth1, null)).toBe(false);
  });
});

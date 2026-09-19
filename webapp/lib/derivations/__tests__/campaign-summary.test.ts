import { describe, expect, it } from "vitest";
import { campaignLineParts } from "../campaign-summary";
import type { RunGroup } from "../campaign-forest";
import type { CampaignRunsWith, CampaignSummary, CycleListEntry } from "@/lib/api";

// The rounds part is read in the CAP's unit — `rounds_closed` counts rounds after the origin,
// which is what `max_rounds` bounds. Every other state of that part (a campaign still at its
// check-in, a declared origin-only run, a fork holding the line) renders a different sentence
// off the same two numbers, and each was a different wrong reading before.

function cycle(over: Partial<CycleListEntry> = {}): CycleListEntry {
  return {
    campaign_id: "spreadsheetbench-s10__00b7d7",
    cycle_id: "cycle_root",
    is_root: true,
    run_phase: "running",
    rounds_closed: 3,
    updated_at: "2026-09-17T00:00:00Z",
    ...over,
  } as CycleListEntry;
}

function run(over: {
  runsWith?: CampaignRunsWith | null;
  root?: CycleListEntry;
  answering?: CycleListEntry;
}): RunGroup {
  const root = over.root ?? cycle();
  return {
    campaign: {
      campaign_id: root.campaign_id,
      dataset_name: "spreadsheetbench-s10",
      label: "",
      runs_with: over.runsWith === undefined ? null : over.runsWith,
    } as CampaignSummary,
    root,
    answering: over.answering ?? root,
    branches: [],
    updatedAt: "2026-09-17T00:00:00Z",
    bestAccuracy: null,
  };
}

const runsWith = (
  params: CampaignRunsWith["params"],
  max_rounds: number | null,
): CampaignRunsWith => ({ params, max_rounds });

describe("campaignLineParts", () => {
  it("leads with the models, de-duplicated and cut to their own name", () => {
    const parts = campaignLineParts(
      run({
        runsWith: runsWith(
          [
            { node: "solve", key: "model", value: "openai/gpt-oss-20b:nitro", source: "campaign" },
            { node: "judge", key: "model", value: "openai/gpt-oss-20b:nitro", source: "dataset" },
          ],
          6,
        ),
      }),
    );
    expect(parts[0]).toBe("gpt-oss-20b:nitro");
    expect(parts.filter((p) => p === "gpt-oss-20b:nitro")).toHaveLength(1);
  });

  it("prints the settings this run chose, never the ones every sibling shares", () => {
    const parts = campaignLineParts(
      run({
        runsWith: runsWith(
          [
            { node: "solve", key: "route_order", value: ["inception"], source: "campaign" },
            { node: "solve", key: "max_turns", value: 20, source: "dataset" },
            { node: "solve", key: "reasoning_effort", value: "high", source: "seed" },
          ],
          6,
        ),
      }),
    );
    expect(parts).toContain("route_order inception");
    expect(parts).toContain("reasoning_effort high");
    expect(parts.some((p) => p.startsWith("max_turns"))).toBe(false);
  });

  it("counts rounds against the declared cap while the root answers", () => {
    const parts = campaignLineParts(run({ runsWith: runsWith([], 6) }));
    expect(parts).toContain("R3/6");
  });

  it("says origin only when the cap declares no round after C0", () => {
    const parts = campaignLineParts(
      run({ runsWith: runsWith([], 0), root: cycle({ rounds_closed: 0 }) }),
    );
    expect(parts).toContain("origin only");
  });

  it("drops the cap when a FORK answers — the cap on screen would be the root's", () => {
    const parts = campaignLineParts(
      run({
        runsWith: runsWith([], 0),
        answering: cycle({ cycle_id: "cycle_fork", is_root: false, rounds_closed: 2 }),
      }),
    );
    expect(parts).toContain("R2");
    expect(parts).not.toContain("origin only");
  });

  it("says nothing about rounds while the campaign is still at its check-in", () => {
    const parts = campaignLineParts(
      run({ runsWith: runsWith([], 6), root: cycle({ run_phase: "checkin", rounds_closed: 0 }) }),
    );
    expect(parts.some((p) => p.startsWith("R") || p === "origin only")).toBe(false);
  });

  it("says the pipeline is unreadable rather than printing an empty setup", () => {
    expect(campaignLineParts(run({ runsWith: null }))[0]).toBe("pipeline unreadable");
  });
});

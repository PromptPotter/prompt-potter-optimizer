import { describe, expect, it } from "vitest";
import {
  campaignLineParts,
  campaignModels,
  campaignTitle,
  campaignVendors,
} from "../campaign-summary";
import { vendorOf } from "@/lib/format";
import type { RunGroup } from "../campaign-forest";
import type { CampaignRunsWith, CampaignSummary, CycleListEntry } from "@/lib/api";

// `rounds_closed` counts rounds after the origin, the unit `max_rounds` bounds. The row is a NAME
// and a reading, never a config dump.

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
  label?: string;
}): RunGroup {
  const root = over.root ?? cycle();
  return {
    campaign: {
      campaign_id: root.campaign_id,
      dataset_name: "spreadsheetbench-s10",
      label: over.label ?? "",
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
  // No served setting reaches the row: models ride the vendor mark, the rest the hover card.
  it("carries no resolved setting at all — the card is where a setup is read", () => {
    const parts = campaignLineParts(
      run({
        runsWith: runsWith(
          [
            { node: "solve", key: "model", value: "openai/gpt-oss-20b:nitro", source: "campaign" },
            { node: "solve", key: "temperature", value: 0, source: "campaign" },
            { node: "solve", key: "reasoning_effort", value: "high", source: "seed" },
            { node: "solve", key: "route_order", value: ["inception"], source: "campaign" },
            { node: "solve", key: "max_turns", value: 20, source: "dataset" },
          ],
          6,
        ),
      }),
    );
    expect(parts).toEqual(["R3/6", expect.stringContaining("ago")]);
  });

  it("counts rounds against the declared cap while the root answers", () => {
    const parts = campaignLineParts(run({ runsWith: runsWith([], 6) }));
    expect(parts).toContain("R3/6");
  });

  // `R0` would read as a run that went nowhere, not one declared never to leave its origin.
  it("says origin when the cap declares no round after C0", () => {
    const parts = campaignLineParts(
      run({ runsWith: runsWith([], 0), root: cycle({ rounds_closed: 0 }) }),
    );
    expect(parts).toContain("origin");
  });

  it("drops the cap when a FORK answers — the cap on screen would be the root's", () => {
    const parts = campaignLineParts(
      run({
        runsWith: runsWith([], 0),
        answering: cycle({ cycle_id: "cycle_fork", is_root: false, rounds_closed: 2 }),
      }),
    );
    expect(parts).toContain("R2");
    expect(parts).not.toContain("origin");
  });

  it("says nothing about rounds while the campaign is still at its check-in", () => {
    const parts = campaignLineParts(
      run({ runsWith: runsWith([], 6), root: cycle({ run_phase: "checkin", rounds_closed: 0 }) }),
    );
    expect(parts.some((p) => p.startsWith("R") || p === "origin")).toBe(false);
  });

  it("says the pipeline is unreadable rather than printing an empty setup", () => {
    expect(campaignLineParts(run({ runsWith: null }))[0]).toBe("pipeline unreadable");
  });
});

describe("vendorOf", () => {
  it("reads the namespace, which is who TRAINED the model", () => {
    expect(vendorOf("openai/gpt-oss-120b")).toBe("openai");
    expect(vendorOf("meta-llama/llama-4-70b")).toBe("meta-llama");
  });

  // Two colons, told apart only by POSITION: a gateway sits left of the slash and names who
  // SERVED the call, the `:nitro` routing suffix sits right of it on the model's own name.
  it("looks past a gateway prefix and through a routing suffix", () => {
    expect(vendorOf("groq:openai/gpt-oss-120b")).toBe("openai");
    expect(vendorOf("qwen/qwen3.7-flash:nitro")).toBe("qwen");
  });

  it("answers with the id itself when it carries no namespace", () => {
    expect(vendorOf("GPT-4")).toBe("gpt-4");
    expect(vendorOf("gpt-4:nitro")).toBe("gpt-4");
  });
});

describe("campaignModels / campaignVendors", () => {
  const twoVendors = run({
    runsWith: runsWith(
      [
        { node: "solve", key: "model", value: "openai/gpt-oss-20b:nitro", source: "campaign" },
        { node: "judge", key: "model", value: "openai/gpt-oss-120b", source: "dataset" },
        { node: "l1_generate", key: "model", value: "deepseek/deepseek-v4-flash", source: "backend" },
        { node: "l1_critique", key: "model", value: "openai/gpt-oss-20b:nitro", source: "backend" },
      ],
      6,
    ),
  });

  it("lists every model whole, de-duplicated — the suffix routes and bills, so it stays", () => {
    expect(campaignModels(twoVendors)).toEqual([
      "openai/gpt-oss-20b:nitro",
      "openai/gpt-oss-120b",
      "deepseek/deepseek-v4-flash",
    ]);
  });

  // Three OpenAI models are ONE brand to count.
  it("collapses models to their vendors, each keeping the ids it stands for", () => {
    expect(campaignVendors(twoVendors)).toEqual([
      { vendor: "openai", models: ["openai/gpt-oss-20b:nitro", "openai/gpt-oss-120b"] },
      { vendor: "deepseek", models: ["deepseek/deepseek-v4-flash"] },
    ]);
  });

  it("has nothing to draw when the pipeline did not resolve", () => {
    expect(campaignVendors(run({ runsWith: null }))).toEqual([]);
  });
});

describe("campaignTitle", () => {
  // The id tail renders exactly while it is all that tells one dataset's runs apart.
  it("keeps the id tail while nothing human distinguishes the campaign", () => {
    expect(campaignTitle(run({}).campaign).suffix).toBe("00b7d7");
  });

  it("drops the id tail once the campaign carries a label", () => {
    expect(campaignTitle(run({ label: "Sheets agent, alibaba pin" }).campaign).suffix).toBeNull();
  });
});

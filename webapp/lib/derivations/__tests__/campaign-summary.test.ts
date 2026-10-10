import { describe, expect, it } from "vitest";
import {
  benchReading,
  campaignLineParts,
  campaignModels,
  campaignVendors,
} from "../campaign-summary";
import type { RunGroup } from "../campaign-forest";
import type { BenchScore, CampaignRunsWith, CampaignSummary, LineStanding } from "@/lib/api";
import { pairedReading } from "@/lib/test-fixtures";

const CAMPAIGN_ID = "spreadsheetbench-s10__00b7d7";

function line(over: Partial<LineStanding> = {}): LineStanding {
  return {
    holder: { campaign_id: CAMPAIGN_ID, cycle_id: "cycle_root" },
    status: { label: "Running", mark: "running" },
    run_phase: "running",
    producer_attached: true,
    stop_reason: null,
    standing: null,
    rounds_closed: 3,
    max_rounds: 6,
    rounds_line: "served rounds",
    rounds_cap_note: null,
    human_intervened: false,
    ...over,
  };
}

function run(over: {
  runsWith?: CampaignRunsWith | null;
  line?: Partial<LineStanding>;
  label?: string;
}): RunGroup {
  const served = line(over.line);
  return {
    campaign: {
      campaign_id: CAMPAIGN_ID,
      dataset_name: "spreadsheetbench-s10",
      label: over.label ?? "",
      line: served,
      updated_at: "2026-09-17T00:00:00Z",
      runs_with: over.runsWith === undefined ? null : over.runsWith,
    } as CampaignSummary,
    line: served,
    branches: [],
  };
}

const runsWith = (
  params: CampaignRunsWith["params"],
  max_rounds: number | null,
): CampaignRunsWith => ({ params, vendors: [], optimizer: "potter", max_rounds });

describe("campaignLineParts", () => {
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
    expect(parts).toEqual(["served rounds", expect.stringContaining("ago")]);
  });

  it("says nothing about rounds while the campaign is still at its check-in", () => {
    const parts = campaignLineParts(
      run({ runsWith: runsWith([], 6), line: { run_phase: "checkin", rounds_closed: 0 } }),
    );
    expect(parts).not.toContain("served rounds");
  });

  it("says the pipeline is unreadable rather than printing an empty setup", () => {
    expect(campaignLineParts(run({ runsWith: null }))[0]).toBe("pipeline unreadable");
  });
});

describe("campaignModels / campaignVendors", () => {
  it("lists every served model whole, in the order of the marks that stand for them", () => {
    const twoVendors = run({
      runsWith: {
        ...runsWith([], 6),
        vendors: [
          { vendor: "openai", models: ["openai/gpt-oss-20b:nitro", "openai/gpt-oss-120b"] },
          { vendor: "deepseek", models: ["deepseek/deepseek-v4-flash"] },
        ],
      },
    });
    expect(campaignModels(twoVendors)).toEqual([
      "openai/gpt-oss-20b:nitro",
      "openai/gpt-oss-120b",
      "deepseek/deepseek-v4-flash",
    ]);
  });

  it("has nothing to draw when the pipeline did not resolve", () => {
    expect(campaignVendors(run({ runsWith: null }))).toEqual([]);
  });
});

describe("benchReading", () => {
  const status = (state: BenchScore["status"]["state"], sentence: string): BenchScore["status"] => ({
    state,
    trigger: "at_end",
    sentence,
    can_grade: false,
    refusal: sentence,
    subject: null,
    held_by: null,
    stop: null,
    scored: null,
    expected: null,
    reads_before: null,
  });

  it("says a bench that is not graded in its served status, never a blank", () => {
    const unheld: BenchScore = {
      bench_size: 0,
      scorer_id: "default_hit",
      headline: "accuracy",
      status: status("not_held", "The campaign's dataset_split holds no bench rows out."),
      origin: null,
      selected: null,
      vs_origin: { ...pairedReading(0, [0, 0]), state: "not_held", coverage: null, headline: null, beside: [] },
      cost: { lift_per_usd: null, absent: "no_lift" },
      line: "The campaign's dataset_split holds no bench rows out.",
    };
    expect(benchReading(unheld).sub).toBe(unheld.line);
    expect(benchReading(null).sub).toBeUndefined();
  });

  it("prints the served level in its column's unit over the served line", () => {
    const banded = (value: number) => ({ value, ci_lo: null, ci_hi: null });
    const reading = { sp_hash: "s", headline: "accuracy" as const, n: 10 };
    const graded: BenchScore = {
      bench_size: 10,
      scorer_id: "default_hit",
      headline: "accuracy",
      status: { ...status("read", "graded"), refusal: "The line's selection is already graded." },
      origin: {
        ...reading,
        round: 0,
        accuracy: banded(0.0),
        composite: banded(0.2),
        level: banded(0.0),
      },
      selected: {
        ...reading,
        round: 3,
        accuracy: banded(0.5),
        composite: banded(0.62),
        level: banded(0.5),
      },
      vs_origin: pairedReading(0.5, [0.2, 0.8], { rateA: 0 }),
      cost: { lift_per_usd: 1.25, absent: null },
      line: "accuracy 0.500 selected (round 3) · 0.000 origin · lift +0.500 · 10 held-out rows",
    };
    const stat = benchReading(graded);
    expect(stat.value).toBe("50%");
    expect(stat.sub).toBe(graded.line);
    const composite = benchReading({
      ...graded,
      headline: "composite",
      selected: { ...graded.selected!, headline: "composite", level: banded(0.62) },
    });
    expect(composite.value).toBe("0.62");
  });
});

import type { ReactNode } from "react";
import type {
  BenchScore,
  ConfigKnob,
  ConfigMapResponse,
  CourseNode,
  MeteredSpend,
  RunStanding,
  RunsWithParam,
  VendorModels,
} from "@/lib/api";
import { CEILING_METER_LABELS } from "@/lib/api/types.generated";
import { phaseIs } from "@/lib/run-phase";
import {
  fmtAgo,
  fmtDateTime,
  fmtFitness,
  fmtPct0,
  fmtTokens,
  fmtUsd,
  fmtValue,
} from "@/lib/format";
import type { RunGroup } from "./campaign-forest";
import { fmtPaired } from "./paired-reading";
import { billText, meteredBucketsLine, ratePricedText, spendHeadline } from "./spend";

export interface RowStat {
  label: string;
  value: string;
  sub?: string;
  className?: string;
}

export interface RowSetting {
  node: string;
  key: string;
  value: string;
  source: RunsWithParam["source"];
}

export interface SummaryFacts {
  title: string;
  // Always a word, never colour alone.
  state?: string | null;
  tags?: string[];
  stats: RowStat[];
}

export interface RowCardFacts extends SummaryFacts {
  lede: string;
  caveat?: ReactNode;
  facts: [string, string][];
  // `null` ⇒ the pipeline did not resolve, which the card says out loud; absent ⇒ not a campaign.
  settings?: RowSetting[] | null;
  campaignId?: string;
}

function settingValue(v: unknown): string {
  return Array.isArray(v) ? v.map((x) => fmtValue(x)).join(",") : fmtValue(v);
}

export const SPEND_STAT_LABEL = "Billed";

export function spendStat(metered: MeteredSpend): RowStat {
  const priced = ratePricedText(metered.rate_priced_usd, metered.calls_rate_priced);
  return {
    label: SPEND_STAT_LABEL,
    value: spendHeadline(metered),
    sub:
      `${priced ? `${priced} · ` : ""}Incurred ${fmtUsd(metered.incurred_usd)} · Counted against cap ` +
      `${fmtUsd(metered.metered_usd)} ${CEILING_METER_LABELS[metered.meter]}: ${meteredBucketsLine(metered)}`,
  };
}

const BENCH_STAT_LABEL = "Bench";

// Accuracy is a rate and the composite a 0–1 score: a score printed as a percent reads as a rate.
function fmtBenchColumn(column: BenchScore["headline"], v: number | null | undefined): string {
  return column === "accuracy" ? fmtPct0(v) : fmtFitness(v ?? null);
}

export function benchStat(bench: BenchScore): RowStat {
  return {
    label: BENCH_STAT_LABEL,
    value: fmtBenchColumn(bench.headline, bench.selected?.level?.value),
    sub: bench.line,
  };
}

export function benchReading(bench: BenchScore | null | undefined): RowStat {
  return bench ? benchStat(bench) : { label: BENCH_STAT_LABEL, value: "—" };
}

// The θ clause only where the served node elects on θ; a peer optimizer's rounds do not.
export function accuracyStat(standing: RunStanding, node: CourseNode | null): RowStat {
  const line = standing.selection_line;
  return {
    label: "Accuracy",
    value: fmtPaired(standing.vs_origin, "rates"),
    sub: node?.elects_on === "ability" ? `${line} · rounds are won on θ` : line,
  };
}

export function campaignVendors(run: RunGroup): VendorModels[] {
  return run.campaign.runs_with?.vendors ?? [];
}

// Full ids: `gpt-oss-20b` versus `gpt-oss-120b` is the distinction a vendor mark cannot draw.
export function campaignModels(run: RunGroup): string[] {
  return campaignVendors(run).flatMap((v) => v.models);
}

export function campaignLineParts(run: RunGroup): string[] {
  const runsWith = run.campaign.runs_with;
  const parts: string[] = [];
  if (runsWith == null) parts.push("pipeline unreadable");
  if (!phaseIs(run.line.run_phase, "authoring")) parts.push(run.line.rounds_line);
  const ago = fmtAgo(run.campaign.updated_at);
  if (ago) parts.push(ago);
  return parts;
}

// What a head-to-head column does not already print: its bench and its spend are rows of their own.
export function courseStats(run: RunGroup, node: CourseNode | null): RowStat[] {
  const { campaign, line } = run;
  const lifetime = campaign.spend_lifetime;
  const standing = line.standing;
  const stats: RowStat[] = [
    {
      label: "Lifetime bill",
      value: billText(lifetime.billed_usd, lifetime.bill_is_floor),
      sub: lifetime.bill_is_floor
        ? `floor — ${fmtTokens(lifetime.unpriced_tokens)} unpriced`
        : lifetime.sends_unreported
          ? `billed · up to ${fmtUsd(lifetime.unreported_usd)} more unreported`
          : (ratePricedText(lifetime.rate_priced_usd, lifetime.calls_rate_priced) ??
            "lifetime, every cycle"),
      className:
        lifetime.bill_is_floor || lifetime.sends_unreported ? "summary-block-warn" : undefined,
    },
    {
      label: "Rounds",
      value: String(line.rounds_closed),
      sub: line.rounds_cap_note ?? undefined,
    },
    ...(standing ? [accuracyStat(standing, node)] : []),
  ];
  if (standing?.stalls_left != null && standing.stalls_left_cap != null) {
    stats.push({ label: "Lives", value: `${standing.stalls_left} / ${standing.stalls_left_cap}` });
  }
  return stats;
}

export function campaignCard(run: RunGroup, node: CourseNode | null): RowCardFacts {
  const { campaign, line } = run;
  const archived = campaign.lifecycle_status === "archived";
  const cycleId = campaign.root_cycle_id;
  const runsWith = campaign.runs_with;

  const facts: [string, string][] = [["Dataset", campaign.dataset_name]];
  if (runsWith) facts.push(["Optimizer", runsWith.optimizer]);
  facts.push(["Comparison", campaign.comparison]);
  facts.push([
    "Last activity",
    fmtAgo(campaign.updated_at) || fmtDateTime(campaign.updated_at),
  ]);
  const created = fmtAgo(campaign.created_at);
  facts.push([
    "Created",
    `${fmtDateTime(campaign.created_at)}${created ? ` · ${created}` : ""}`,
  ]);
  facts.push(["Campaign", campaign.campaign_id]);
  facts.push(["Cycle", cycleId]);
  if (line.holder.cycle_id !== cycleId) facts.push(["Answering", line.holder.cycle_id]);

  const stats: RowStat[] = [
    ...(campaign.bench ? [benchStat(campaign.bench)] : []),
    spendStat(campaign.spend_metered),
    ...courseStats(run, node),
  ];

  return {
    title: campaign.display_name,
    state: line.status.label,
    tags: line.human_intervened ? ["babysat"] : undefined,
    lede: archived
      ? "Archived — restore it from the ⋯ menu to open it."
      : "The campaign and the course it ran. Its origin is the C0 row inside it.",
    stats,
    facts,
    settings: runsWith
      ? runsWith.params.map((p) => ({
          node: p.node,
          key: p.key,
          value: settingValue(p.value),
          source: p.source,
        }))
      : null,
    campaignId: campaign.campaign_id,
  };
}

// A knob moving several estimands is listed under each, so the path dedupes it.
export function declaredKnobs(map: ConfigMapResponse): ConfigKnob[] {
  const byPath = new Map<string, ConfigKnob>();
  for (const group of map.groups) {
    for (const knob of group.knobs) {
      const scalar = knob.value == null || typeof knob.value !== "object";
      if (knob.source === "campaign" && scalar) byPath.set(knob.path, knob);
    }
  }
  return [...byPath.values()];
}

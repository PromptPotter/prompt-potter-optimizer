// One reading of a campaign for the row line, hover card and masthead, so no two surfaces word
// it two ways. Formatting only — every number already arrived on the wire.

import type { ReactNode } from "react";
import type {
  BenchScore,
  CampaignSummary,
  ConfigKnob,
  ConfigMapResponse,
  LineageNode,
  MeteredSpend,
  RunsWithParam,
  VendorModels,
} from "@/lib/api";
import { campaignDisplayName } from "@/lib/names";
import { runPhaseLabel, runPhaseMark, type RunPhaseMark } from "@/lib/run-phase";
import {
  fmtAgo,
  fmtDateTime,
  fmtFitness,
  fmtPct0,
  fmtSigned,
  fmtTokens,
  fmtUsd,
  fmtValue,
  shortId,
} from "@/lib/format";
import type { RunGroup } from "./campaign-forest";
import { METER_WORD, billText, meteredBucketsLine, spendHeadline } from "./spend";

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

// What `shell/SummaryBlock` draws, for each host that summarises a campaign.
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
  // Present ⇒ the on-disk breakdown and declared config are fetched, lazily while open.
  campaignId?: string;
}

function settingValue(v: unknown): string {
  return Array.isArray(v) ? v.map((x) => fmtValue(x)).join(",") : fmtValue(v);
}

export const SPEND_STAT_LABEL = "Billed";

// The bill leads; what the same calls would have cost with every replay priced, and what the cap
// counts of either, sit beside it — an arm replaying a sibling's cells bills $0 for them.
export function spendStat(metered: MeteredSpend): RowStat {
  return {
    label: SPEND_STAT_LABEL,
    value: spendHeadline(metered),
    sub:
      `Incurred ${fmtUsd(metered.incurred_usd)} · Counted against cap ` +
      `${fmtUsd(metered.metered_usd)} ${METER_WORD[metered.meter]}: ${meteredBucketsLine(metered)}`,
  };
}

// The headline: the selection graded on held-out rows no optimizer node read, in the column the
// score names (`headline`), with the other column beside it; every value served, and a pass that
// read nothing shows the served reason in place of a number.
export const BENCH_STAT_LABEL = "Bench";

// Accuracy is a rate and the composite a 0–1 score: a score printed as a percent reads as a rate.
function fmtBenchColumn(column: BenchScore["headline"], v: number | null | undefined): string {
  return column === "accuracy" ? fmtPct0(v) : fmtFitness(v ?? null);
}

export function benchStat(bench: BenchScore): RowStat {
  const { selected, origin, missing_reason, headline } = bench;
  const beside = headline === "accuracy" ? "composite" : "accuracy";
  return {
    label: BENCH_STAT_LABEL,
    value: fmtBenchColumn(headline, selected?.[headline]?.value),
    sub:
      missing_reason !== null
        ? missing_reason
        : `${headline} · origin ${fmtBenchColumn(headline, origin?.[headline]?.value)} · ` +
          `lift ${fmtSigned(bench.lift[headline]?.value)} · ` +
          `${beside} ${fmtBenchColumn(beside, selected?.[beside]?.value)} · ` +
          `${bench.bench_size} held-out rows`,
  };
}

// A split holding nothing out is served as a score with its `missing_reason`; a null score comes
// with the served reason no pass was taken (`bench_missing_reason`).
export function benchReading(
  bench: BenchScore | null | undefined,
  missingReason: string | null | undefined,
): RowStat {
  // Guarded: `dashboard.json` is served verbatim, and a file an older build wrote names no column.
  if (bench?.headline) return benchStat(bench);
  return { label: BENCH_STAT_LABEL, value: "—", sub: missingReason ?? undefined };
}

// The θ clause only where the served node elects on θ: a peer optimizer's rounds are not.
export function accuracyStat(
  origin: number | null,
  best: number | null,
  node: LineageNode | null,
): RowStat {
  const lifted = origin != null && best != null && best !== origin;
  const which = lifted ? "origin → best" : origin != null ? "origin" : "best";
  return {
    label: "Accuracy",
    value: lifted ? `${fmtPct0(origin)} → ${fmtPct0(best)}` : fmtPct0(origin ?? best),
    sub: node?.stamps_theta ? `${which} · rounds are won on θ` : which,
  };
}

// `suffix` (the `__xxxxxx` tail) survives only while it is all that tells one dataset's runs
// apart, so a campaign named via Rename (`label`) drops it.
export function campaignTitle(c: CampaignSummary): {
  dataset: string;
  label: string;
  suffix: string | null;
} {
  const tail = shortId(c.campaign_id);
  return {
    dataset: c.dataset_name,
    label: c.label,
    suffix: c.label || tail === c.campaign_id ? null : tail,
  };
}

export interface RowStatus {
  mark: RunPhaseMark;
  word: string;
}

const ARCHIVED: RowStatus = { mark: { glyph: "▫", tone: "quiet" }, word: "Archived" };

// One cycle's served phase as a row mark — a campaign row, a fork row and an inner run alike.
export function phaseStatus(
  runPhase: string | null | undefined,
  reason: LineageNode["stop_reason"] | undefined,
): RowStatus {
  return { mark: runPhaseMark(runPhase, reason), word: runPhaseLabel(runPhase, reason) };
}

// Off the ANSWERING cycle, so the sidebar row and the masthead switcher cannot disagree.
export function campaignStatus(run: RunGroup): RowStatus {
  if (run.campaign.lifecycle_status === "archived") return ARCHIVED;
  return phaseStatus(run.answering.run_phase, run.answering.stop_reason);
}

// After a supersede cut the line continues on a fork, which carries its own cap and rounds.
function rootAnswers(run: RunGroup): boolean {
  return run.answering.cycle_id === run.root.cycle_id;
}

// `rounds_closed` counts rounds after the origin, which is the unit `max_rounds` bounds.
function roundsPart(run: RunGroup, cap: number | null): string {
  if (!rootAnswers(run)) return `R${run.answering.rounds_closed}`;
  // Capped at zero, it measured its origin and stopped; `R0` would read as a run that went nowhere.
  if (cap === 0) return "origin";
  return cap == null
    ? `R${run.answering.rounds_closed}`
    : `R${run.answering.rounds_closed}/${cap}`;
}

// Served whole: which param is a model and whose it is are the pipeline resolution's answers.
export function campaignVendors(run: RunGroup): VendorModels[] {
  return run.campaign.runs_with?.vendors ?? [];
}

// Full ids: `gpt-oss-20b` versus `gpt-oss-120b` is the distinction a vendor mark cannot draw.
export function campaignModels(run: RunGroup): string[] {
  return campaignVendors(run).flatMap((v) => v.models);
}

// No resolved setting rides this line: no served predicate picks the ones worth the space
// (`source` is not one), so the hover card carries them all.
export function campaignLineParts(run: RunGroup): string[] {
  const runsWith = run.campaign.runs_with;
  const parts: string[] = [];
  if (runsWith == null) parts.push("pipeline unreadable");
  if (run.answering.run_phase !== "checkin") {
    parts.push(roundsPart(run, runsWith ? runsWith.max_rounds : null));
  }
  const ago = fmtAgo(run.updatedAt);
  if (ago) parts.push(ago);
  return parts;
}

// `node` (the served lineage root) is absent until the row is expanded.
export function campaignCard(
  run: RunGroup,
  node: LineageNode | null,
  state: string | undefined,
): RowCardFacts {
  const { campaign, answering } = run;
  const archived = campaign.lifecycle_status === "archived";
  const cycleId = run.root.cycle_id;
  const runsWith = campaign.runs_with;

  const facts: [string, string][] = [["Dataset", campaign.dataset_name]];
  if (runsWith) facts.push(["Optimizer", runsWith.optimizer]);
  facts.push([
    "Comparison",
    campaign.arm
      ? `controlled — arm ${campaign.arm.arm_key} of head-to-head ${campaign.arm.head_to_head_id}`
      : "not controlled — optimizes with every measurement and steer",
  ]);
  facts.push(["Last activity", fmtAgo(run.updatedAt) || fmtDateTime(run.updatedAt)]);
  const created = fmtAgo(campaign.created_at);
  facts.push([
    "Created",
    `${fmtDateTime(campaign.created_at)}${created ? ` · ${created}` : ""}`,
  ]);
  facts.push(["Campaign", campaign.campaign_id]);
  facts.push(["Cycle", cycleId]);
  if (answering.cycle_id !== cycleId) facts.push(["Answering", answering.cycle_id]);

  const cap = runsWith ? runsWith.max_rounds : null;
  const lifetime = campaign.spend_lifetime;
  const stats: RowStat[] = [
    ...(campaign.bench ? [benchStat(campaign.bench)] : []),
    spendStat(campaign.spend_metered),
    {
      label: "Lifetime bill",
      value: billText(lifetime.billed_usd, lifetime.bill_is_floor),
      sub: lifetime.bill_is_floor
        ? `floor — ${fmtTokens(lifetime.unpriced_tokens)} unpriced`
        : lifetime.sends_unreported
          ? `billed · up to ${fmtUsd(lifetime.unreported_usd)} more unreported`
          : "lifetime, every cycle",
      className:
        lifetime.bill_is_floor || lifetime.sends_unreported ? "summary-block-warn" : undefined,
    },
    {
      label: "Rounds",
      value: String(answering.rounds_closed),
      sub: !rootAnswers(run)
        ? "fork answers — its cap is on its dashboard"
        : cap === 0
          ? "origin only"
          : cap != null
            ? `of ${cap} — rounds cap`
            : undefined,
    },
    accuracyStat(
      node?.origin_accuracy ?? null,
      run.bestAccuracy ?? node?.best_accuracy ?? null,
      node,
    ),
  ];
  const standing = node?.run_standing;
  if (standing?.stalls_left != null && standing.stalls_left_cap != null) {
    stats.push({ label: "Lives", value: `${standing.stalls_left} / ${standing.stalls_left_cap}` });
  }

  return {
    title: campaignDisplayName(campaign),
    state,
    tags: answering.human_intervened ? ["babysat"] : undefined,
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

// A knob moving several estimands is listed under each, so the path dedupes it; a nested value
// is the config-map panel's to render, never a JSON blob here.
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

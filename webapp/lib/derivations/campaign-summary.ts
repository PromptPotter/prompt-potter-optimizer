// One reading of a campaign for the row line, hover card and masthead, so no two surfaces word
// it two ways. Formatting only — every number already arrived on the wire.

import type { ReactNode } from "react";
import type {
  CampaignSummary,
  ConfigKnob,
  ConfigMapResponse,
  LineageNode,
  RunsWithParam,
} from "@/lib/api";
import { campaignDisplayName } from "@/lib/names";
import { runPhaseLabel, runPhaseMark, type RunPhaseMark } from "@/lib/run-phase";
import {
  fmtAgo,
  fmtDateTime,
  fmtPct0,
  fmtTokens,
  fmtUsd,
  fmtUsdCents,
  fmtValue,
  shortId,
  vendorOf,
} from "@/lib/format";
import type { RunGroup } from "./campaign-forest";

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

export interface RowCardFacts {
  title: string;
  // Always a word, never colour alone.
  state?: string | null;
  tags?: string[];
  lede: string;
  stats: RowStat[];
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

// A price-less model makes the spend a FLOOR, and the `≥` says so on the row itself.
function spendFloor(c: CampaignSummary): string {
  return c.spend_unpriced_tokens > 0 ? "≥" : "";
}

export function spendLabel(c: CampaignSummary): string {
  return `${spendFloor(c)}${fmtUsdCents(c.spend_used_usd)}`;
}

export function accuracyStat(origin: number | null, best: number | null): RowStat {
  const lifted = origin != null && best != null && best !== origin;
  return {
    label: "Accuracy",
    value: lifted ? `${fmtPct0(origin)} → ${fmtPct0(best)}` : fmtPct0(origin ?? best),
    sub: `${lifted ? "origin → best" : origin != null ? "origin" : "best"} · rounds are won on θ`,
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
  reason: string | null | undefined,
): RowStatus {
  return { mark: runPhaseMark(runPhase, reason), word: runPhaseLabel(runPhase, reason) };
}

// Off the ANSWERING cycle, so the sidebar row and the masthead switcher cannot disagree.
export function campaignStatus(run: RunGroup): RowStatus {
  if (run.campaign.lifecycle_status === "archived") return ARCHIVED;
  return phaseStatus(run.answering.run_phase, run.answering.status);
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

// Full ids: `gpt-oss-20b` versus `gpt-oss-120b` is the distinction a vendor mark cannot draw.
export function campaignModels(run: RunGroup): string[] {
  const runsWith = run.campaign.runs_with;
  if (runsWith == null) return [];
  const seen = new Set<string>();
  for (const p of runsWith.params) {
    if (p.key === "model" && p.value != null) seen.add(settingValue(p.value));
  }
  return [...seen];
}

export function campaignVendors(run: RunGroup): { vendor: string; models: string[] }[] {
  const byVendor = new Map<string, string[]>();
  for (const model of campaignModels(run)) {
    const arr = byVendor.get(vendorOf(model));
    if (arr) arr.push(model);
    else byVendor.set(vendorOf(model), [model]);
  }
  return [...byVendor].map(([vendor, models]) => ({ vendor, models }));
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
  const stats: RowStat[] = [
    {
      label: "Spend",
      value: `${spendFloor(campaign)}${fmtUsd(campaign.spend_used_usd)}`,
      sub:
        campaign.spend_unpriced_tokens > 0
          ? `floor — ${fmtTokens(campaign.spend_unpriced_tokens)} unpriced`
          : campaign.spend_unreported_usd > 0
            ? `billed · up to ${fmtUsd(campaign.spend_unreported_usd)} more unreported`
            : "lifetime, every cycle",
      className:
        campaign.spend_unpriced_tokens > 0 || campaign.spend_unreported_usd > 0
          ? "rowhover-tone-warn"
          : undefined,
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
    accuracyStat(node?.origin_accuracy ?? null, run.bestAccuracy ?? node?.best_accuracy ?? null),
  ];
  if (node?.hearts != null && node.lives_cap != null) {
    stats.push({ label: "Lives", value: `${node.hearts} / ${node.lives_cap}` });
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

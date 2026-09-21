// ONE reading of a campaign — the row line, the hover card and the masthead all take it from
// here, so two surfaces cannot word one campaign two ways. Pure formatting of served values:
// `runs_with` is the campaign list's transport of the root pipeline resolution, and every
// number below already arrived on the wire.

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
  // A second line under the value — which floor a Δ is against, what a figure leaves out.
  sub?: string;
  className?: string;
}

// One resolved pipeline setting, already rendered: the node it rides, the config grid's own key,
// the value whole, and the layer that won it.
export interface RowSetting {
  node: string;
  key: string;
  value: string;
  source: RunsWithParam["source"];
}

export interface RowCardFacts {
  title: string;
  // The row's run-state or verdict word — always a word, never colour alone.
  state?: string | null;
  tags?: string[];
  lede: string;
  stats: RowStat[];
  // A served caveat that makes a number above unreadable as it stands.
  caveat?: ReactNode;
  facts: [string, string][];
  // What the root course runs with. `null` ⇒ the pipeline did not resolve, which is a state the
  // card says out loud; absent ⇒ this row is not a campaign.
  settings?: RowSetting[] | null;
  // Present ⇒ the on-disk breakdown and the declared config are fetched (lazily — the card
  // mounts only while open).
  campaignId?: string;
}

// A served param value as one line — a list joins on its own separator so a route reads as a
// route, not as JSON.
function settingValue(v: unknown): string {
  return Array.isArray(v) ? v.map((x) => fmtValue(x)).join(",") : fmtValue(v);
}

// A campaign's served lifetime spend. A price-less model makes it a FLOOR, and the `≥` says so
// on the row itself rather than only inside the card.
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

// The three parts of a campaign's name, for a surface that renders them apart. `label` is the
// operator's alone and is empty when they set none.
//
// `suffix` is the `__xxxxxx` tail, and it is present exactly while it is the only thing telling
// ten runs of one dataset apart — so a NAMED campaign drops it. The tail is an id, and a row
// wearing `spreadsheetbench-s20__94f842` and nothing else reads as an attribute rather than a
// thing; but dropping it from an unnamed row would leave nine siblings identical, which is
// worse. Naming a campaign (⋯ → Rename, which writes `label`) is what buys the tail away.
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

// A campaign row's run-state: the glyph a narrow column can hold, and the word that names it.
export interface RowStatus {
  mark: RunPhaseMark;
  word: string;
}

// An archived campaign moves nowhere, so the phase its last cycle stopped on is not what the
// row reports.
const ARCHIVED: RowStatus = { mark: { glyph: "▫", tone: "quiet" }, word: "Archived" };

// Off the cycle that ANSWERS for the campaign, so the sidebar row and the masthead switcher
// cannot word one campaign's state two ways.
export function campaignStatus(run: RunGroup): RowStatus {
  if (run.campaign.lifecycle_status === "archived") return ARCHIVED;
  const { run_phase, status } = run.answering;
  return { mark: runPhaseMark(run_phase, status), word: runPhaseLabel(run_phase, status) };
}

// Does the campaign's ROOT still answer for it? After a supersede cut the line continues on a
// fork, which carries its own cap and its own rounds.
function rootAnswers(run: RunGroup): boolean {
  return run.answering.cycle_id === run.root.cycle_id;
}

// The rounds part, in the CAP's own unit: `rounds_closed` counts rounds after the origin, which
// is what `max_rounds` bounds.
function roundsPart(run: RunGroup, cap: number | null): string {
  if (!rootAnswers(run)) return `R${run.answering.rounds_closed}`;
  // One word, and it is the repo's own — a campaign capped at zero rounds measured its origin
  // and stopped. `R0` is the reading this refuses: that reads as a run that went nowhere.
  if (cap === 0) return "origin";
  return cap == null
    ? `R${run.answering.rounds_closed}`
    : `R${run.answering.rounds_closed}/${cap}`;
}

// Every model this campaign runs, de-duplicated, in the order its params declare them. Full
// ids: this is what names the vendor mark, and `gpt-oss-20b` versus `gpt-oss-120b` is exactly
// the distinction a mark on its own cannot draw.
export function campaignModels(run: RunGroup): string[] {
  const runsWith = run.campaign.runs_with;
  if (runsWith == null) return [];
  const seen = new Set<string>();
  for (const p of runsWith.params) {
    if (p.key === "model" && p.value != null) seen.add(settingValue(p.value));
  }
  return [...seen];
}

// The VENDORS behind those models, de-duplicated a SECOND time: a campaign running three OpenAI
// models is one brand to count, and a column of repeated marks would say otherwise. Each keeps
// the ids it stands for, because the mark is named by them.
export function campaignVendors(run: RunGroup): { vendor: string; models: string[] }[] {
  const byVendor = new Map<string, string[]>();
  for (const model of campaignModels(run)) {
    const arr = byVendor.get(vendorOf(model));
    if (arr) arr.push(model);
    else byVendor.set(vendorOf(model), [model]);
  }
  return [...byVendor].map(([vendor, models]) => ({ vendor, models }));
}

// What tells one campaign row from the next, in reading order: how far it got, and when it last
// moved. Parts, not a string, so a caller picks its own separator.
//
// **No resolved SETTING rides this line, and the reason is that no predicate can pick the ones
// worth the space.** `source: campaign | seed` was that predicate — it reads as "the operator
// chose this" — and it is not one: every harbor campaign declares `temperature: 0`, so the row
// wore `temperature 0` on some campaigns and nothing on others, which reads as a difference
// between them and is not. Interestingness is not a served fact, and a client-side guess at it
// is the browser deciding what a number means. So the row carries none and the hover card
// carries them ALL, whole and wearing the layer that won each (`campaignCard::settings`) — one
// hover away, where an audit is answered instead of scanned. The MODELS left the same way
// earlier, to the vendor mark (`campaignVendors`), which reads as a column where a slug reads
// as more text.
export function campaignLineParts(run: RunGroup): string[] {
  const runsWith = run.campaign.runs_with;
  const parts: string[] = [];
  // The one setup fact a row still states, because it is a FAILURE to resolve rather than a
  // value: nothing below can be trusted to describe what this campaign runs.
  if (runsWith == null) parts.push("pipeline unreadable");
  // A campaign still at its origin check-in has run no rounds, and "R0" would read as one that
  // ran and got nowhere.
  if (run.answering.run_phase !== "checkin") {
    parts.push(roundsPart(run, runsWith ? runsWith.max_rounds : null));
  }
  const ago = fmtAgo(run.updatedAt);
  if (ago) parts.push(ago);
  return parts;
}

// The hover card for a CAMPAIGN row — the campaign and the course it ran, whichever cycle
// currently answers for it. `node` is the served root of its lineage tree, absent until the row
// is expanded, so every reading off it is optional.
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

// The knobs an operator DECLARED for this campaign, out of every estimand group that names one.
// A knob moving several estimands is listed by each of them, so the path is what makes it one
// row; a nested value is the config-map panel's to render, never a JSON blob in a hover card.
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

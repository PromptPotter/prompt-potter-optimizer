// Origin check-in client helpers. The mint gate is `origin_readiness.py`'s, served as
// `draft.readiness` — never re-derive it here.

import type { DraftCampaignWire, DraftPatch } from "./api";

// Mirrors the field ids `origin_readiness.py` keys `draft.field_provenance` by. Config keys are
// settable by a resolver question but not gated.
export const ORIGIN_KEY = {
  columnQuery: "column.query",
  columnGroundTruth: "column.ground_truth",
  taskDescription: "task_description",
  connector: "connector",
  scoringComposite: "scoring_composite",
  maxRounds: "max_rounds",
  backendNodeConfig: "backend.node_config",
} as const;

export function questionPatch(field: string, answer: string): DraftPatch | null {
  const value = answer.trim();
  if (!value) return null;
  switch (field) {
    case ORIGIN_KEY.columnQuery:
      return { column_query: value };
    case ORIGIN_KEY.columnGroundTruth:
      return { column_ground_truth: value };
    case ORIGIN_KEY.taskDescription:
      return { raw_task_description: value };
    case ORIGIN_KEY.connector:
      return { connector: value };
    case ORIGIN_KEY.scoringComposite:
      return { scoring_composite: value };
    case ORIGIN_KEY.maxRounds: {
      // 0 is legal — "measure the origin and stop" (server `ge=0`); never guard `>= 1`.
      const n = Number(value);
      return Number.isInteger(n) && n >= 0 && n <= 100
        ? { optimization_overrides: { max_rounds: n } }
        : null;
    }
    default:
      return null;
  }
}

export function questionOptions(field: string, options: string[], headers: string[]): string[] {
  if (options.length > 0) return options;
  if (field === ORIGIN_KEY.columnQuery || field === ORIGIN_KEY.columnGroundTruth) return headers;
  return [];
}

const CONNECTOR_LABELS: Record<string, string> = { termnorm: "the TermNorm pipeline" };
const SCORER_LABELS: Record<string, string> = {
  label_match: "an exact match against the target",
};

function shortTaskTitle(task: string): string | null {
  const firstLine = task
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l.length > 0);
  if (!firstLine) return null;
  const cleaned = firstLine.replace(/^#+\s*/, "").replace(/[.\s]+$/, "").trim();
  return cleaned.length > 0 && cleaned.length <= 72 ? cleaned : null;
}

export function plainLanguageRecap(draft: DraftCampaignWire): string {
  const input = draft.column_query || "your input";
  const target = draft.column_ground_truth || "the target";
  const scorer = SCORER_LABELS[draft.scoring_composite] ?? draft.scoring_composite;
  const connector = CONNECTOR_LABELS[draft.connector] ?? draft.connector;
  const maxRounds = draft.optimization_overrides.max_rounds;
  const rounds = maxRounds === 1 ? "1 round" : `up to ${maxRounds} rounds`;

  const title = shortTaskTitle(draft.raw_task_description);
  const lead = title
    ? `evolve a prompt for “${title}”`
    : `evolve a prompt that turns each “${input}” into the right “${target}”`;

  return `PromptPotter will ${lead} — running ${connector}, scoring by ${scorer}, over ${rounds}.`;
}

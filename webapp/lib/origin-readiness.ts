// The mint gate is `origin_readiness.py`'s, served as `draft.readiness` — never re-derive it here.

import type { DraftPatch } from "./api";

// Mirrors the field ids `origin_readiness.py` keys `draft.field_provenance` by.
export const ORIGIN_KEY = {
  columnQuery: "column.query",
  columnGroundTruth: "column.ground_truth",
  taskDescription: "task_description",
  connector: "connector",
  scoringMatcher: "scoring_matcher",
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
    case ORIGIN_KEY.scoringMatcher:
      return { scoring_matcher: value };
    default:
      return null;
  }
}

export function questionOptions(field: string, options: string[], headers: string[]): string[] {
  if (options.length > 0) return options;
  if (field === ORIGIN_KEY.columnQuery || field === ORIGIN_KEY.columnGroundTruth) return headers;
  return [];
}

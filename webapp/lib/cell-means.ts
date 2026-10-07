// The terms a composite fitness prices a cell on, as served means over one point's own cells
// (`SubjectReading.cell_means`). ONE table, so every surface naming a mean words and formats it
// alike. Indexed through a guard by its readers: a channel no cell carries is absent.
//
// ORDER is the reading order: the two `lead` rows are what an operator checks first on a new
// leader — a prompt that got shorter, and a cell that got faster.

import { fmtMetricValue } from "@/lib/format";

export interface CellMeanRow {
  key: string;
  label: string;
  fmt: (v: number) => string;
  lead?: true;
}

export const CELL_MEAN_ROWS: readonly CellMeanRow[] = [
  {
    key: "target_prompt_chars",
    label: "prompt length",
    fmt: (v) => `${Math.round(v).toLocaleString()} chars`,
    lead: true,
  },
  { key: "latency", label: "avg time / cell", fmt: (v) => fmtMetricValue("seconds", v), lead: true },
  { key: "cost", label: "avg cost / cell", fmt: (v) => fmtMetricValue("usd", v) },
  { key: "tokens", label: "avg tokens / cell", fmt: (v) => fmtMetricValue("tokens", v) },
];

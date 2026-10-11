// served: `SubjectReading.cell_means`; a channel no cell carries is absent.

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

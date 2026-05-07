// Parser for compact per-sample lines emitted by LiveDashboardProjection.
// See `fmt_sample_line` in promptpotter/infrastructure/projections/live_dashboard.py.
// Shape: "  0.0s #000 HIT  [ai]📖 -> '...' gt:'...' q:'...'"

export interface ParsedSample {
  raw?: string;
  elapsed?: number;
  idx?: number;
  status?: "HIT" | "MISS";
  scorer?: string;
  predicted?: string;
  gt?: string;
  query?: string;
}

export const SAMPLE_RE =
  /^\s*([\d.]+)s\s+#(\d+)\s+(HIT|MISS)\s+\[([^\]]*)\][^>]*->\s*'([\s\S]*?)'\s+gt:'([\s\S]*?)'\s+q:'([\s\S]*?)'\s*$/;

export function parseSampleLine(line: string): ParsedSample {
  const m = line.match(SAMPLE_RE);
  if (!m) return { raw: line };
  return {
    elapsed: parseFloat(m[1]),
    idx: parseInt(m[2], 10),
    status: m[3] as "HIT" | "MISS",
    scorer: m[4],
    predicted: m[5],
    gt: m[6],
    query: m[7],
  };
}

// Parser for compact per-sample lines emitted by LiveDashboardProjection.
// See `fmt_sample_line` in promptpotter/infrastructure/projections/live_dashboard/render.py.
// Shape: "  0.0s #000 sid:014 HIT  [ai]📖 -> '...' gt:'...' q:'...'"
// The elapsed column is BLANK where the row recorded no time (an errored row that never
// reached the pipeline); a cached replay's real 0.0 still prints.
// `idx` = iteration position within the candidate's loop (qi).
// `sampleId` = dataset sample_id — diverges from qi once the Rasch sorter
// drives the iteration order; the heatmap consumer uses sampleId.

interface ParsedSample {
  raw?: string;
  elapsed?: number;
  idx?: number;
  sampleId?: number;
  // `ERR` carries no fitness to reconstruct (`lib/types/sample.ts::SampleStatus`).
  status?: "HIT" | "MISS" | "ERR";
  scorer?: string;
  // True when the line carries the 📖 cache glyph (measurement reused from a
  // prior identical searchpoint). The glyph sits outside the scorer bracket,
  // so it's read off the raw line, not a regex group.
  cached?: boolean;
  predicted?: string;
  gt?: string;
  query?: string;
}

const SAMPLE_RE =
  /^\s*(?:([\d.]+)s)?\s+#(\d+)(?:\s+sid:(\d+))?\s+(HIT|MISS|ERR)\s+\[([^\]]*)\][^>]*->\s*'([\s\S]*?)'\s+gt:'([\s\S]*?)'\s+q:'([\s\S]*?)'\s*$/;

export function parseSampleLine(line: string): ParsedSample {
  const m = line.match(SAMPLE_RE);
  if (!m) return { raw: line };
  return {
    elapsed: m[1] != null ? parseFloat(m[1]) : undefined,
    idx: parseInt(m[2]!, 10),
    sampleId: m[3] != null ? parseInt(m[3], 10) : undefined,
    status: m[4] as "HIT" | "MISS" | "ERR",
    scorer: m[5],
    cached: line.includes("📖"),
    predicted: m[6],
    gt: m[7],
    query: m[8],
  };
}

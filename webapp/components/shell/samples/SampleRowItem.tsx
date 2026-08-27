"use client";
import type { SampleRow } from "@/lib/types";

// How many of a candidate's rows are put in the DOM before the rest become a count. A rendering
// bound, never a claim about the data: both surfaces print the remainder rather than dropping it,
// and the served tallies beside them are over the whole set. One constant because it is one
// number — two of them, with a "mirrors the other" note, is a value nobody can change once.
export const SAMPLE_RENDER_CAP = 250;

function truncate(s: string | undefined, n: number): string {
  if (!s) return "—";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// One per-sample row inside a candidate group, expanding to query / ground-truth /
// predicted. Pure renderer — the host owns the source + selection state. Chrome rather than
// dashboard-local: the round-wide samples table and the searchpoint drill-in both render it, and
// the drill-in is itself on two tabs.
export function SampleRowItem({ row }: { row: SampleRow }) {
  // Three marks, three tags — `ERR` shares no colour with `MISS`
  // (`lib/types/sample.ts::SampleStatus`).
  const tag =
    row.status === "HIT" ? "tag-hit" : row.status === "ERR" ? "tag-err" : "tag-miss";
  const pred = row.predicted ? row.predicted : "∅";
  return (
    <details className="rsv-row">
      <summary>
        <span className={tag}>{row.status ?? "—"}</span>
        <span className="idx">
          #{String(row.sample_id ?? "").padStart(3, "0")}
        </span>
        {row.elapsed_s != null && (
          <span className="elapsed">{row.elapsed_s.toFixed(1)}s</span>
        )}
        {row.terminal_node && <span className="scorer">{row.terminal_node}</span>}
        {row.cached && (
          <span
            className="rsv-cached"
            title="Reused from a prior identical searchpoint — no fresh backend call"
          >
            📖 cached
          </span>
        )}
        <span className="body">
          gt:{truncate(row.ground_truth, 22)} · pred:{truncate(pred, 22)}
          {row.query ? ` · q:${truncate(row.query, 36)}` : ""}
        </span>
      </summary>
      <div className="rsv-detail">
        <div>
          <span className="kv-label">query</span>
          <span>{row.query || "—"}</span>
        </div>
        <div>
          <span className="kv-label">ground truth</span>
          <span>{row.ground_truth || "—"}</span>
        </div>
        <div>
          <span className="kv-label">predicted</span>
          <span>{pred}</span>
        </div>
      </div>
    </details>
  );
}

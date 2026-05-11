"use client";
import { TERMS } from "@/lib/terms";

interface ResultRow {
  id?: string | number;
  query?: string;
  predicted?: string;
  ground_truth?: string;
  score?: number;
  error?: unknown;
}

interface RoundData {
  results?: ResultRow[];
  origin_accuracy?: number;
}

interface Props {
  round: RoundData | null;
}

function isHit(r: ResultRow): boolean {
  if (typeof r.score === "number") return r.score >= 0.5;
  if (r.error) return false;
  if (r.predicted && r.ground_truth) {
    return String(r.predicted).includes(String(r.ground_truth).replace("#### ", "").trim());
  }
  return false;
}

export function EvalTable({ round }: Props) {
  const rows = round?.results ?? [];
  return (
    <div className="card">
      <div className="card-title">
        <span>Per-sample eval</span>
        <span className="badge">round</span>
      </div>
      <div className="table-wrap">
        {rows.length === 0 ? (
          <div style={{ color: "var(--color-text-tertiary)", padding: 16, fontSize: 13, textAlign: "center" }}>
            First round in progress. Each round mutates the prompt, scores it on the dataset, then critiques. Hold tight (~10–60s depending on dataset size).
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th title={TERMS.col_id}>ID</th>
                <th title={TERMS.col_status}>Status</th>
                <th title={TERMS.col_query}>Query</th>
                <th title={TERMS.col_predicted}>Predicted</th>
                <th title={TERMS.col_ground}>Ground</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 200).map((r, i) => (
                <tr key={String(r.id ?? i)}>
                  <td>{String(r.id ?? i)}</td>
                  <td>
                    {isHit(r) ? <span className="pass">HIT</span> : <span className="fail">MISS</span>}
                  </td>
                  <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.query}>
                    {r.query || "—"}
                  </td>
                  <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.predicted}>
                    {r.predicted || "—"}
                  </td>
                  <td style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.ground_truth}>
                    {r.ground_truth || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

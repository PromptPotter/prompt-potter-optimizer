"use client";
import { useState } from "react";
import { CardFrame } from "@/components/ui/card";
import { Badge } from "@/components/ui/Badge";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

interface ScoreboardEntry {
  rank?: number;
  candidate_id?: string;
  label?: string;
  accuracy?: number;
  composite_fitness?: number;
  hits?: number;
  total?: number;
  is_winner?: boolean;
}

interface ResultRow {
  sample_id?: string | number;
  id?: string | number;
  query?: string;
  predicted?: string;
  ground_truth?: string;
  hit?: boolean;
  fitness?: number;
  score?: number;
  error?: unknown;
  cached?: boolean;
}

export interface RoundDoc {
  round?: number;
  accuracy?: number;
  composite_fitness?: number;
  hits?: number;
  total?: number;
  origin_accuracy?: number;
  improved?: boolean;
  p_value?: number;
  scoreboard?: ScoreboardEntry[];
  results?: ResultRow[];
}

interface Props {
  doc: RoundDoc;
  raw: string;
}

function isHit(r: ResultRow): boolean {
  if (typeof r.hit === "boolean") return r.hit;
  if (typeof r.fitness === "number") return r.fitness >= 0.5;
  if (typeof r.score === "number") return r.score >= 0.5;
  if (r.error) return false;
  if (r.predicted && r.ground_truth) {
    return String(r.predicted).includes(String(r.ground_truth).replace("#### ", "").trim());
  }
  return false;
}

function pct(n: number | undefined): string {
  if (typeof n !== "number" || !isFinite(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

function num(n: number | undefined, digits = 3): string {
  if (typeof n !== "number" || !isFinite(n)) return "—";
  return n.toFixed(digits);
}

export function RoundFileView({ doc, raw }: Props) {
  const [showRaw, setShowRaw] = useState(false);
  const results = doc.results ?? [];
  const scoreboard = doc.scoreboard ?? [];
  const hits = doc.hits ?? results.filter(isHit).length;
  const total = doc.total ?? results.length;

  return (
    <RotatePrompt surfaceName="The round file view">
    <div className="round-file-view">
      <div className="round-file-summary">
        <div className="round-file-summary-row">
          <Badge>round {doc.round ?? "—"}</Badge>
          <span>accuracy {pct(doc.accuracy)} {typeof doc.origin_accuracy === "number" && (<span style={{ color: "var(--color-text-tertiary)" }}>(origin {pct(doc.origin_accuracy)})</span>)}</span>
          <span>composite {num(doc.composite_fitness)}</span>
          <span>{hits}/{total} hits</span>
          {typeof doc.p_value === "number" && <span>p {num(doc.p_value, 3)}</span>}
          {doc.improved ? <span className="pass">improved</span> : <span style={{ color: "var(--color-text-tertiary)" }}>no improvement</span>}
        </div>
      </div>

      {scoreboard.length > 0 && (
        <CardFrame
          style={{ margin: "8px 0" }}
          title={<span>Scoreboard</span>}
          actions={<Badge>{scoreboard.length}</Badge>}
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Candidate</th>
                  <th>Accuracy</th>
                  <th>Composite</th>
                  <th>Hits</th>
                  <th>Win</th>
                </tr>
              </thead>
              <tbody>
                {scoreboard.map((s, i) => (
                  <tr key={s.candidate_id ?? i}>
                    <td>{s.rank ?? i + 1}</td>
                    <td style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={s.label}>
                      {s.label || s.candidate_id || "—"}
                    </td>
                    <td>{pct(s.accuracy)}</td>
                    <td>{num(s.composite_fitness)}</td>
                    <td>{s.hits ?? "—"}/{s.total ?? "—"}</td>
                    <td>{s.is_winner ? <span className="pass">win</span> : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardFrame>
      )}

      {results.length > 0 && (
        <CardFrame
          style={{ margin: "8px 0" }}
          title={<span>Per-sample results</span>}
          actions={<Badge>{results.length}</Badge>}
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th title="Sample ID — stable identifier from the project.">ID</th>
                  <th title="Hit / miss for this sample.">Status</th>
                  <th title="Input given to the pipeline for this sample.">Query</th>
                  <th title="Top-1 prediction returned by the pipeline.">Predicted</th>
                  <th title="Ground-truth answer from the project.">Ground</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => {
                  const hit = isHit(r);
                  const id = r.sample_id ?? r.id ?? i;
                  return (
                    <tr key={String(id)}>
                      <td>{String(id)}</td>
                      <td>{hit ? <span className="pass">HIT</span> : <span className="fail">MISS</span>}</td>
                      <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.query}>
                        {r.query || "—"}
                      </td>
                      <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.predicted}>
                        {r.predicted || "—"}
                      </td>
                      <td style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.ground_truth}>
                        {r.ground_truth || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardFrame>
      )}

      <details open={showRaw} onToggle={(e) => setShowRaw((e.target as HTMLDetailsElement).open)} style={{ margin: "8px 0" }}>
        <summary style={{ cursor: "pointer", padding: "8px 0", color: "var(--color-text-secondary)", fontSize: 13 }}>
          Raw JSON ({raw.length.toLocaleString()} chars)
        </summary>
        <pre style={{ fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.5, whiteSpace: "pre-wrap", color: "var(--color-text-secondary)", background: "var(--color-background-secondary)", padding: 10, borderRadius: "var(--border-radius-md)", marginTop: 6 }}>
          {raw}
        </pre>
      </details>
    </div>
    </RotatePrompt>
  );
}

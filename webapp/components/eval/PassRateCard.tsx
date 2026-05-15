"use client";
import { TERMS } from "@/lib/terms";

interface Candidate {
  candidate_id?: string;
  label?: string;
  composite_fitness?: number;
  accuracy?: number;
  is_winner?: boolean;
}

interface RoundData {
  scoreboard?: Candidate[];
  origin_accuracy?: number;
  composite_fitness?: number;
  accuracy?: number;
}

interface Props {
  round: RoundData | null;
}

export function PassRateCard({ round }: Props) {
  const sb = round?.scoreboard ?? [];
  const origin = round?.origin_accuracy ?? 0;
  const top = sb.slice(0, 3);
  const max = Math.max(...top.map((c) => c.composite_fitness ?? c.accuracy ?? 0), 0.001);

  return (
    <div className="card">
      <div className="card-title">
        <span title={TERMS.composite}>Pass Rate (composite)</span>
        <span className="badge" title={TERMS.badge_top}>top</span>
      </div>
      {top.length === 0 ? (
        <div style={{ color: "var(--color-text-tertiary)", fontSize: 13 }}>
          {round
            ? "No candidates this round."
            : "First round in progress. Each round mutates the prompt, scores it on the project, then critiques. Hold tight (~10–60s depending on project size)."}
        </div>
      ) : (
        top.map((c, i) => {
          const v = c.composite_fitness ?? c.accuracy ?? 0;
          const pct = (v / max) * 100;
          const color = (c.accuracy ?? 0) >= origin ? "var(--color-accent)" : "var(--color-accent-strong)";
          const label = (c.label || c.candidate_id || "").slice(0, 80);
          return (
            <div key={c.candidate_id || i} className="bar-row">
              <div className="bar-label">
                <span title={label}>{label || "—"}</span>
                <span>{v.toFixed(3)}</span>
              </div>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

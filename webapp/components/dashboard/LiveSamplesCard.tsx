"use client";
import type { DashboardSnapshot } from "@/lib/poll";
import { parseSampleLine } from "@/lib/sample-line";

interface Props {
  dash: DashboardSnapshot | null;
}

interface CandidateLike {
  idx?: number;
  samples?: string[];
}

export function LiveSamplesCard({ dash }: Props) {
  const score = (dash?.current_round?.nodes as Record<string, { output?: { candidates?: CandidateLike[] } }> | undefined)?.l1_score;
  const cands = score?.output?.candidates ?? [];

  // Flatten across candidates, tagging each sample with its candidate idx.
  const rows: { candIdx: number | undefined; raw: string }[] = [];
  for (const c of cands) {
    const samples = c.samples ?? [];
    for (const s of samples) rows.push({ candIdx: c.idx, raw: s });
  }

  // Newest first, cap at 100.
  const latest = rows.slice().reverse().slice(0, 100);

  return (
    <div className="card samples-card">
      <h2 className="card-title">
        Live samples
        <span className="badge">rolling · {latest.length} shown</span>
      </h2>
      <div className="samples-list" role="log" aria-live="polite" aria-atomic="false">
        {latest.length === 0 ? (
          <div className="samples-empty">
            No samples scored yet this round. They&apos;ll appear here as the optimizer runs the dataset against the current candidate.
          </div>
        ) : (
          latest.map((r, i) => {
            const p = parseSampleLine(r.raw);
            if (p.raw) {
              return (
                <div key={i} className="row">
                  <span className="body">{p.raw}</span>
                </div>
              );
            }
            const tag = p.status === "HIT" ? "tag-hit" : "tag-miss";
            const body = `gt:'${p.gt}' pred:'${p.predicted || "∅"}' q:'${p.query}'`;
            return (
              <div key={i} className="row" title={r.raw}>
                <span className={tag}>{p.status}</span>
                <span className="idx">#{String(p.idx ?? "").padStart(3, "0")}</span>
                <span className="elapsed">{(p.elapsed ?? 0).toFixed(1)}s</span>
                <span className="body">[c{r.candIdx ?? "?"}/{p.scorer}] {body}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

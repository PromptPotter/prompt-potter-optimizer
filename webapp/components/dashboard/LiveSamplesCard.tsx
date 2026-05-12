"use client";
import { roundOf, type DashboardSnapshot, type StatusKind } from "@/lib/poll";
import { parseSampleLine } from "@/lib/sample-line";
import { candidateLabel } from "@/lib/candidate-label";

interface Props {
  dash: DashboardSnapshot | null;
  status: StatusKind;
}

interface CandidateLike {
  idx?: number;
  label?: string;
  samples?: string[];
}

export function LiveSamplesCard({ dash, status }: Props) {
  const score = (dash?.current_round?.nodes as Record<string, { output?: { candidates?: CandidateLike[] } }> | undefined)?.l1_score;
  const cands = score?.output?.candidates ?? [];
  const roundNum = roundOf(dash) ?? 0;

  // Flatten across candidates, tagging each sample with its candidate label.
  const rows: { candLabel: string; raw: string }[] = [];
  for (const c of cands) {
    const label = c.label || candidateLabel(roundNum, c.idx);
    const samples = c.samples ?? [];
    for (const s of samples) rows.push({ candLabel: label, raw: s });
  }

  // Newest first, cap at 200.
  const latest = rows.slice().reverse().slice(0, 200);

  // Liveness label tracks the status banner: "rolling" only when the
  // optimizer is actively writing, "snapshot" once the dashboard is frozen.
  // Without this, a stale dashboard kept claiming to be rolling for samples
  // that hadn't moved in minutes.
  const liveness = status === "live" ? "rolling" : "snapshot";

  return (
    <div className="card samples-card">
      <h2 className="card-title">
        Live samples
        <span className="badge">{liveness} · {latest.length} shown</span>
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
                <span className="body">[{r.candLabel}/{p.scorer}] {body}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

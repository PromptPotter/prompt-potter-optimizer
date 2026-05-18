"use client";
import { liveL1Candidates, type DashboardSnapshot, type StatusKind } from "@/lib/poll";
import { parseSampleLine } from "@/lib/sample-line";
import { candidateLabel } from "@/lib/candidate-label";

interface Props {
  dash: DashboardSnapshot | null;
  dashRound: number | null;
  status: StatusKind;
}

export function LiveSamplesCard({ dash, dashRound, status }: Props) {
  const cands = liveL1Candidates(dash);
  const roundNum = dashRound ?? 0;

  // Flatten across candidates, tagging each sample with its candidate label.
  // Live samples arrive as compact strings from LiveDashboardProjection; full
  // dict rows are only emitted post-round and don't surface in this card.
  const rows: { candLabel: string; raw: string }[] = [];
  for (const c of cands) {
    const label = c.label || candidateLabel(roundNum, c.idx);
    for (const s of c.samples ?? []) {
      if (typeof s === "string") rows.push({ candLabel: label, raw: s });
    }
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
          <EmptyState status={status} dashRound={dashRound} />
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

// Three-way to stop conflating "loop is idle" with "no data exists." The
// old single-message variant nagged the operator about a candidate run
// even after they'd selected a stopped cycle whose verdict was already on
// disk in earlier rounds.
function EmptyState({ status, dashRound }: { status: StatusKind; dashRound: number | null }) {
  if (status === "live") {
    return (
      <div className="samples-empty">
        No samples scored yet this round. They&apos;ll appear here as the optimizer runs the project against the current candidate.
      </div>
    );
  }
  if (dashRound != null && dashRound > 0) {
    return (
      <div className="samples-empty">
        Loop is idle. Earlier rounds&apos; samples are in the Verdict panel below.
      </div>
    );
  }
  return (
    <div className="samples-empty">
      This cycle hasn&apos;t run yet. Start it with{" "}
      <code>python -m promptpotter resume</code>.
    </div>
  );
}

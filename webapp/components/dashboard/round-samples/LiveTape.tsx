"use client";
import { useMemo } from "react";
import {
  liveL1Candidates,
  type DashboardSnapshot,
  type StatusKind,
} from "@/lib/poll";
import { parseSampleLine } from "@/lib/sample-line";
import { candidateLabel } from "@/lib/candidate-label";

interface Props {
  dash: DashboardSnapshot | null;
  status: StatusKind;
  round: number;
}

interface Row {
  key: string;
  candLabel: string;
  candIdx: number;
  raw: string;
}

// Rolling per-candidate stream of compact sample lines from the in-flight
// round. Replaces the prior LiveSamplesCard's flat tape:
//   - stable React keys (round | cand | slot | raw-prefix) instead of
//     array index on a reversed list, so newcomers don't shift others'
//     DOM identity and break expand state.
//   - per-candidate group headers so the operator can see who's running
//     hot vs cold without scanning labels in every row.
//   - rows are <details> — click to reveal full query / gt / pred without
//     truncation.
//   - predicted renders ∅ when missing — the projection's source-field
//     bug that made every prediction empty was fixed at the Python side
//     (see live_dashboard/view.py append_sample): the inbound key was
//     ``prediction`` but scored results carry ``predicted``.
export function LiveTape({ dash, status, round }: Props) {
  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    for (const c of liveL1Candidates(dash)) {
      const cIdx = Number(c.idx);
      const label = c.label || candidateLabel(round, cIdx);
      (c.samples ?? []).forEach((s, sIdx) => {
        if (typeof s !== "string") return;
        const key = `${round}|${cIdx}|${sIdx}|${s.slice(0, 32)}`;
        out.push({ key, candLabel: label, candIdx: cIdx, raw: s });
      });
    }
    // Newest first; cap stops the DOM from growing unbounded on long rounds.
    return out.slice().reverse().slice(0, 400);
  }, [dash, round]);

  const groups = useMemo(() => {
    // Preserve newest-first order across groups by keying on first
    // appearance order in the already-reversed `rows` list.
    const map = new Map<string, Row[]>();
    for (const r of rows) {
      let g = map.get(r.candLabel);
      if (!g) {
        g = [];
        map.set(r.candLabel, g);
      }
      g.push(r);
    }
    return [...map.entries()];
  }, [rows]);

  if (rows.length === 0) {
    return <EmptyState status={status} />;
  }

  return (
    <div className="live-tape" role="log" aria-live="polite" aria-atomic="false">
      {groups.map(([label, items]) => {
        const hits = items.reduce(
          (n, r) => n + (parseSampleLine(r.raw).status === "HIT" ? 1 : 0),
          0,
        );
        const misses = items.length - hits;
        return (
          <div key={label} className="tape-group">
            <div className="tape-group-head">
              <span className="tape-cand-label">{label}</span>
              <span className="tape-tally">
                <span className="tag-hit">HIT {hits}</span>
                <span className="tag-miss">MISS {misses}</span>
              </span>
            </div>
            {items.map((r) => (
              <TapeRow key={r.key} raw={r.raw} />
            ))}
          </div>
        );
      })}
    </div>
  );
}

function TapeRow({ raw }: { raw: string }) {
  const p = parseSampleLine(raw);
  if (p.raw) {
    return (
      <div className="tape-row tape-row-raw">
        <span className="body">{p.raw}</span>
      </div>
    );
  }
  const tag = p.status === "HIT" ? "tag-hit" : "tag-miss";
  const pred = p.predicted ? p.predicted : "∅";
  return (
    <details className="tape-row">
      <summary>
        <span className={tag}>{p.status}</span>
        <span className="idx">#{String(p.idx ?? "").padStart(3, "0")}</span>
        <span className="elapsed">{(p.elapsed ?? 0).toFixed(1)}s</span>
        <span className="scorer">{p.scorer || "—"}</span>
        <span className="body">
          gt:{truncate(p.gt, 22)} · pred:{truncate(pred, 22)} · q:
          {truncate(p.query, 36)}
        </span>
      </summary>
      <div className="tape-detail">
        <div>
          <span className="kv-label">query</span>
          <span>{p.query || "—"}</span>
        </div>
        <div>
          <span className="kv-label">ground truth</span>
          <span>{p.gt || "—"}</span>
        </div>
        <div>
          <span className="kv-label">predicted</span>
          <span>{pred}</span>
        </div>
      </div>
    </details>
  );
}

function truncate(s: string | undefined, n: number): string {
  if (!s) return "—";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// Three-way to keep the three "no rows" reasons distinct, same as the
// prior implementation — but the round-tabs strip now scopes which
// round we'd be empty for, so the operator can always jump back to a
// completed round without typing.
function EmptyState({ status }: { status: StatusKind }) {
  if (status === "live") {
    return (
      <div className="samples-empty">
        No samples scored yet this round. They&apos;ll appear here as the
        optimizer runs the project against the current candidate.
      </div>
    );
  }
  return (
    <div className="samples-empty">
      Loop is idle. Click a numbered round above to inspect its completed
      samples.
    </div>
  );
}

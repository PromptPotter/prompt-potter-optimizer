"use client";
import { useMemo, useState } from "react";
import { useRoundFile } from "@/lib/useRoundFile";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  round: number;
}

// Schema reverse-engineered from on-disk round_NNNN.json:
//   scoreboard[]              — per-candidate stats + rank + label
//   all_candidate_results[cid] — list of per-sample results for candidate cid
// The per_sample[] field on scoreboard entries is also defined but stays
// empty on current campaigns; all_candidate_results is the live source.
interface ScoreboardEntry {
  rank?: number;
  candidate_id?: string;
  label?: string;
  accuracy?: number;
}

interface RawSample {
  sample_id?: number;
  query?: string;
  predicted?: string;
  ground_truth?: string;
  hit?: boolean;
  fitness?: number;
  error?: unknown;
}

interface SampleRow {
  candidate_id: string;
  candidate_label: string;
  candidate_rank: number;
  sample_id: number;
  query: string;
  predicted: string;
  ground_truth: string;
  hit: boolean | null;
  hasError: boolean;
}

type SortKey = "rank" | "sample" | "status";
type StatusFilter = "all" | "hit" | "miss";

// Hard ceiling on rendered rows. With 8 candidates × 20 samples per round
// the typical table sits well under this; the cap keeps a sweep over 100
// rounds (where some carry hundreds of rows) from blowing up the DOM.
const ROW_CAP = 500;

export function RoundSamplesTable({ campaignId, cycleId, round }: Props) {
  const { doc, loading, error } = useRoundFile(campaignId, cycleId, round);
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [candFilter, setCandFilter] = useState<string>("all");

  const candidates = useMemo<ScoreboardEntry[]>(
    () => (doc?.scoreboard as ScoreboardEntry[] | undefined) ?? [],
    [doc],
  );

  const rows = useMemo<SampleRow[]>(() => {
    if (!doc) return [];
    const acr = doc.all_candidate_results as
      | Record<string, unknown>
      | undefined;
    if (!acr) return [];
    const out: SampleRow[] = [];
    for (const c of candidates) {
      const cid = c.candidate_id ?? "";
      if (!cid) continue;
      const list = acr[cid];
      if (!Array.isArray(list)) continue;
      const label = c.label || `R${round}.${c.rank ?? 1}`;
      const rank = typeof c.rank === "number" ? c.rank : 99;
      for (const sample of list) {
        const s = sample as RawSample;
        out.push({
          candidate_id: cid,
          candidate_label: label,
          candidate_rank: rank,
          sample_id: typeof s.sample_id === "number" ? s.sample_id : -1,
          query: typeof s.query === "string" ? s.query : "",
          predicted: typeof s.predicted === "string" ? s.predicted : "",
          ground_truth:
            typeof s.ground_truth === "string" ? s.ground_truth : "",
          hit: typeof s.hit === "boolean" ? s.hit : null,
          hasError: s.error != null && s.error !== "",
        });
      }
    }
    return out;
  }, [doc, candidates, round]);

  const filtered = useMemo<SampleRow[]>(() => {
    return rows
      .filter((r) => {
        if (statusFilter === "all") return true;
        if (statusFilter === "hit") return r.hit === true;
        return r.hit === false;
      })
      .filter((r) => candFilter === "all" || r.candidate_id === candFilter)
      .sort((a, b) => {
        if (sortKey === "rank") {
          return (
            a.candidate_rank - b.candidate_rank ||
            a.sample_id - b.sample_id
          );
        }
        if (sortKey === "sample") {
          return (
            a.sample_id - b.sample_id ||
            a.candidate_rank - b.candidate_rank
          );
        }
        // status: HIT (0) → MISS (1) → unknown (2)
        const av = a.hit === true ? 0 : a.hit === false ? 1 : 2;
        const bv = b.hit === true ? 0 : b.hit === false ? 1 : 2;
        return av - bv || a.candidate_rank - b.candidate_rank;
      })
      .slice(0, ROW_CAP);
  }, [rows, sortKey, statusFilter, candFilter]);

  if (loading) {
    return <div className="samples-empty">Loading round {round}…</div>;
  }
  if (error) {
    return (
      <div className="samples-empty">
        Could not load round {round}: {error}
      </div>
    );
  }
  if (!doc) {
    return (
      <div className="samples-empty">Round {round} file not on disk.</div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="samples-empty">
        Round {round} carries no per-sample results.
      </div>
    );
  }

  return (
    <div className="round-samples-table-wrap">
      <div className="rst-filters">
        <select
          value={candFilter}
          onChange={(e) => setCandFilter(e.target.value)}
          aria-label="Filter by candidate"
          className="rst-cand-select"
        >
          <option value="all">All candidates ({candidates.length})</option>
          {candidates.map((c) => (
            <option key={c.candidate_id} value={c.candidate_id}>
              {c.label || `R${round}.${c.rank ?? 1}`}
              {typeof c.accuracy === "number"
                ? ` · ${(c.accuracy * 100).toFixed(0)}%`
                : ""}
            </option>
          ))}
        </select>
        <div role="group" className="rst-toggle" aria-label="HIT/MISS filter">
          {(["all", "hit", "miss"] as StatusFilter[]).map((f) => (
            <button
              key={f}
              type="button"
              className={statusFilter === f ? "on" : ""}
              onClick={() => setStatusFilter(f)}
            >
              {f.toUpperCase()}
            </button>
          ))}
        </div>
        <div role="group" className="rst-toggle" aria-label="Sort">
          <span className="rst-toggle-label">sort</span>
          {(["rank", "sample", "status"] as SortKey[]).map((k) => (
            <button
              key={k}
              type="button"
              className={sortKey === k ? "on" : ""}
              onClick={() => setSortKey(k)}
            >
              {k}
            </button>
          ))}
        </div>
        <span className="rst-count">
          {filtered.length} of {rows.length}
          {rows.length > ROW_CAP ? ` (cap ${ROW_CAP})` : ""}
        </span>
      </div>
      <div className="rst-table-scroll">
        <table className="rst-table">
          <thead>
            <tr>
              <th>cand</th>
              <th>sample</th>
              <th>status</th>
              <th>query</th>
              <th>ground truth</th>
              <th>predicted</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={`${r.candidate_id}-${r.sample_id}`}>
                <td className="rst-cand">{r.candidate_label}</td>
                <td className="mono">
                  #{String(r.sample_id).padStart(3, "0")}
                </td>
                <td>
                  <span
                    className={
                      r.hit === true
                        ? "tag-hit"
                        : r.hit === false
                          ? "tag-miss"
                          : ""
                    }
                  >
                    {r.hit === true ? "HIT" : r.hit === false ? "MISS" : "—"}
                  </span>
                </td>
                <td className="rst-ellipsis" title={r.query}>
                  {r.query}
                </td>
                <td className="rst-ellipsis" title={r.ground_truth}>
                  {r.ground_truth}
                </td>
                <td className="rst-ellipsis" title={r.predicted}>
                  {r.predicted || (r.hasError ? "[error]" : "∅")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

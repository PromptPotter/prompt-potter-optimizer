"use client";
// Cross-campaign measurement leverage — the "your data accumulates" view.
// Reads GET /api/v1/measurements/leverage and renders a table sorted by
// reuse intensity. M13 Slice C; see docs/specs/m13-chat-first-user-web.md.

import { fetchLeverage, type PerQueryRow } from "@/lib/api";
import { fmtPct0, fmtFitness, ageText } from "@/lib/format";
import { useFetch } from "@/lib/useFetch";
import { Loading, ErrorNote } from "@/components/ui/states";

const ROW_LIMIT = 200;

function truncate(s: string, n: number): string {
  const trimmed = s.replace(/\s+/g, " ").trim();
  return trimmed.length > n ? `${trimmed.slice(0, n)}…` : trimmed;
}

export function LeveragePanel() {
  const { data, error } = useFetch((s) => fetchLeverage(ROW_LIMIT, s), []);

  if (error) {
    return (
      <div className="leverage-pane">
        <ErrorNote>Failed to load leverage: {error}</ErrorNote>
      </div>
    );
  }

  if (data === null) {
    return (
      <div className="leverage-pane">
        <Loading>Loading accumulated measurements…</Loading>
      </div>
    );
  }

  if (data.n_measurements === 0) {
    return (
      <div className="leverage-pane">
        <div className="leverage-empty">
          <h2>No measurements yet</h2>
          <p>
            Run a campaign to start accumulating measurements. Once two campaigns
            touch the same query, you&apos;ll see the reuse here.
          </p>
        </div>
      </div>
    );
  }

  const reusedQueries = data.per_query.filter((r) => r.n_measurements >= 2).length;

  return (
    <div className="leverage-pane">
      <header className="leverage-header">
        <h2>Accumulated measurements</h2>
        <p className="leverage-subtitle">
          Every query measured across every campaign on this install. Queries
          that appear in more than one campaign are reused — work you don&apos;t
          have to redo.
        </p>
      </header>
      <div className="leverage-stats">
        <div className="leverage-stat">
          <div className="leverage-stat-value">{data.n_measurements.toLocaleString()}</div>
          <div className="leverage-stat-label">total measurements</div>
        </div>
        <div className="leverage-stat">
          <div className="leverage-stat-value">{data.n_runs.toLocaleString()}</div>
          <div className="leverage-stat-label">campaign runs</div>
        </div>
        <div className="leverage-stat">
          <div className="leverage-stat-value">{data.n_unique_queries.toLocaleString()}</div>
          <div className="leverage-stat-label">unique queries</div>
        </div>
        <div className="leverage-stat">
          <div className="leverage-stat-value">{reusedQueries.toLocaleString()}</div>
          <div className="leverage-stat-label">reused (≥2×)</div>
        </div>
      </div>
      <div className="leverage-table-wrap">
        <table className="leverage-table">
          <thead>
            <tr>
              <th title="Sample id from the project that introduced this query.">ID</th>
              <th>Query</th>
              <th title="How many times this query has been measured across all campaigns.">#</th>
              <th title="Distinct campaign configurations that measured this query.">Configs</th>
              <th title="Mean composite fitness across every measurement of this query.">Mean fitness</th>
              <th title="Fraction of measurements where the pipeline output matched ground truth.">Hit rate</th>
              <th title="Most recent measurement on this query.">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {data.per_query.map((r) => (
              <LeverageRow key={`${r.sample_id}-${r.query.slice(0, 32)}`} row={r} />
            ))}
          </tbody>
        </table>
        {data.n_unique_queries > data.per_query.length && (
          <div className="leverage-truncated">
            Showing top {data.per_query.length} of {data.n_unique_queries.toLocaleString()} queries.
          </div>
        )}
      </div>
    </div>
  );
}

function LeverageRow({ row }: { row: PerQueryRow }) {
  return (
    <tr>
      <td className="leverage-id">{row.sample_id}</td>
      <td className="leverage-query" title={row.query}>
        {truncate(row.query, 120)}
      </td>
      <td className="leverage-num"><strong>{row.n_measurements}</strong></td>
      <td className="leverage-num">{row.n_unique_configs}</td>
      <td className="leverage-num">{fmtFitness(row.mean_fitness)}</td>
      <td className="leverage-num">{fmtPct0(row.hit_rate)}</td>
      <td className="leverage-when">{ageText(row.last_seen)}</td>
    </tr>
  );
}

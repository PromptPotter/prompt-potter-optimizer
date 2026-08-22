"use client";

import type { DatasetIndexEntry, OriginEntry } from "@/lib/api";
import { fmtPct0 } from "@/lib/format";

// New Campaign entry list — two scrollable menus (≤8 rows each), one click:
//   Origins  → reuse a real, runnable origin (from `GET /origins` — campaign-
//              backed or prepared). Opens it editable, then Start; skips the
//              checkin node, graph enters at l1_generate.
//   Datasets → make a NEW origin from raw material via the check-in assistant.
// The two are now distinct surfaces: origins are starting points that exist
// (run or prepared), datasets are raw material that still needs an origin.
export function DatasetPickList({
  origins,
  datasets,
  onOpenOrigin,
  onPick,
  busy,
}: {
  origins: OriginEntry[];
  datasets: DatasetIndexEntry[];
  onOpenOrigin: (entry: OriginEntry) => void;
  onPick: (entry: DatasetIndexEntry) => void;
  busy: boolean;
}) {
  if (origins.length === 0 && datasets.length === 0) {
    return (
      <div className="chat-msg ai ingest-picklist">
        <p>Drop a CSV, TSV, JSON, or Excel file below to start — any column names work.</p>
      </div>
    );
  }

  // "N campaigns · last 42%", or "prepared" for an origin with no campaign yet.
  const originMeta = (o: OriginEntry): string => {
    if (o.n_campaigns === 0) return "prepared";
    const acc = o.origin_accuracy == null ? "" : ` · ${fmtPct0(o.origin_accuracy)}`;
    return `${o.n_campaigns} campaign${o.n_campaigns === 1 ? "" : "s"}${acc}`;
  };

  return (
    <div className="chat-msg ai ingest-picklist">
      <p>Reuse an origin, set up a new one from a dataset, or drop a file.</p>

      {origins.length > 0 ? (
        <div className="ingest-picklist-group">
          <span className="ingest-picklist-head">Origins</span>
          <ul className="ingest-picklist-items ingest-picklist-scroll">
            {origins.map((o) => (
              <li key={`${o.origin_id}:${o.dataset_name}`}>
                <button
                  type="button"
                  className="ingest-pick-btn"
                  disabled={busy}
                  title="Open this origin to review, then start"
                  onClick={() => onOpenOrigin(o)}
                >
                  <span className="ingest-pick-name">
                    {o.label ? `${o.dataset_name} — ${o.label}` : o.dataset_name}
                  </span>
                  <span className="ingest-pick-meta">{originMeta(o)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {datasets.length > 0 ? (
        <div className="ingest-picklist-group">
          <span className="ingest-picklist-head">Datasets</span>
          <ul className="ingest-picklist-items ingest-picklist-scroll">
            {datasets.map((d) => (
              <li key={d.name}>
                <button
                  type="button"
                  className="ingest-pick-btn"
                  disabled={busy}
                  title="Make an origin with the check-in assistant"
                  onClick={() => onPick(d)}
                >
                  <span className="ingest-pick-name">
                    {d.title ? `${d.name} — ${d.title}` : d.name}
                  </span>
                  <span className="ingest-pick-meta">{d.n_samples ?? "—"}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

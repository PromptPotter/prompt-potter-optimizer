"use client";

import type { DatasetIndexEntry } from "@/lib/api";

// New Campaign entry list — two scrollable menus (≤8 rows each), one click:
//   Origins  → reuse a dataset's committed origin (opens it editable, then Start;
//              skips the checkin node, graph enters at l1_generate).
//   Datasets → make a NEW origin via the check-in assistant.
// Benchmarks/demos are datasets, so both menus list the whole collection.
export function DatasetPickList({
  datasets,
  onOpenOrigin,
  onPick,
  busy,
}: {
  datasets: DatasetIndexEntry[];
  onOpenOrigin: (entry: DatasetIndexEntry) => void;
  onPick: (entry: DatasetIndexEntry) => void;
  busy: boolean;
}) {
  if (datasets.length === 0) {
    return (
      <div className="chat-msg ai ingest-picklist">
        <p>Drop a CSV, TSV, JSON, or Excel file below to start — any column names work.</p>
      </div>
    );
  }

  const menu = (title: string, act: (e: DatasetIndexEntry) => void, hint: string) => (
    <div className="ingest-picklist-group">
      <span className="ingest-picklist-head">{title}</span>
      <ul className="ingest-picklist-items ingest-picklist-scroll">
        {datasets.map((d) => (
          <li key={d.name}>
            <button
              type="button"
              className="ingest-pick-btn"
              disabled={busy}
              title={hint}
              onClick={() => act(d)}
            >
              <span className="ingest-pick-name">{d.title ? `${d.name} — ${d.title}` : d.name}</span>
              <span className="ingest-pick-meta">{d.n_samples}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );

  return (
    <div className="chat-msg ai ingest-picklist">
      <p>Reuse an origin, set up a new one from a dataset, or drop a file.</p>
      {menu("Origins", onOpenOrigin, "Open this origin to edit, then start")}
      {menu("Datasets", onPick, "Make an origin with the check-in assistant")}
    </div>
  );
}

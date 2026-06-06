"use client";

import type { DatasetIndexEntry } from "@/lib/api";

// The entry state of the unified ingest conversation: the operator's registered
// datasets and the bundled benchmarks/demos, each one click away from a new
// origin. Picking any of them runs the SAME draft → context → check-in path a
// dropped file does (the hook's `pickDataset`), prefilling the context box with
// the dataset's known task; there is no separate "re-run" affordance.
export function DatasetPickList({
  datasets,
  onPick,
  busy,
}: {
  datasets: DatasetIndexEntry[];
  onPick: (entry: DatasetIndexEntry) => void;
  busy: boolean;
}) {
  const owned = datasets.filter((d) => d.tier === "yours");
  const ready = datasets.filter((d) => d.tier === "benchmark" || d.tier === "demo");

  if (owned.length === 0 && ready.length === 0) {
    return (
      <div className="chat-msg ai ingest-picklist">
        <p>
          Drop a CSV, TSV, JSON, or Excel file below to start your first dataset —
          any column names work. I’ll read it and set up the campaign.
        </p>
      </div>
    );
  }

  return (
    <div className="chat-msg ai ingest-picklist">
      <p>Pick a dataset to start a campaign, or drop a file below.</p>
      {owned.length > 0 ? (
        <DatasetGroup title="Your datasets" items={owned} onPick={onPick} busy={busy} />
      ) : null}
      {ready.length > 0 ? (
        <DatasetGroup
          title="Benchmarks &amp; demos"
          items={ready}
          onPick={onPick}
          busy={busy}
        />
      ) : null}
    </div>
  );
}

function DatasetGroup({
  title,
  items,
  onPick,
  busy,
}: {
  title: string;
  items: DatasetIndexEntry[];
  onPick: (entry: DatasetIndexEntry) => void;
  busy: boolean;
}) {
  return (
    <div className="ingest-picklist-group">
      <span className="ingest-picklist-head">{title}</span>
      <ul className="ingest-picklist-items">
        {items.map((d) => (
          <li key={d.name}>
            <button
              type="button"
              className="ingest-pick-btn"
              disabled={busy}
              onClick={() => onPick(d)}
            >
              <span className="ingest-pick-name">{d.title ? `${d.name} — ${d.title}` : d.name}</span>
              <span className="ingest-pick-meta">{d.n_samples} samples</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

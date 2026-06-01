"use client";

import { useState } from "react";
import { IngestApiError, postMintCampaign, type DatasetIndexEntry } from "@/lib/api";
import type { OnMinted } from "./types";

// ----- Non-empty branch — list collection, pick one, mint -------------------

export function ListAndMintFlow({
  owned,
  benchmarks,
  busy,
  onClose,
  onMinted,
  onPickDemo,
  onAddOrigin,
}: {
  owned: DatasetIndexEntry[];
  benchmarks: DatasetIndexEntry[];
  busy: boolean;
  onClose: () => void;
  onMinted?: OnMinted;
  onPickDemo: (entry: DatasetIndexEntry) => void;
  onAddOrigin: () => void;
}) {
  const [picked, setPicked] = useState<string>(owned[0]?.name ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const byName = new Map<string, DatasetIndexEntry>();
  for (const d of [...owned, ...benchmarks]) byName.set(d.name, d);

  const handleStart = async () => {
    if (!picked) return;
    // A demo runs the prefilled setup wizard (the parent owns the draft) — never
    // an instant mint — exactly like picking it from the empty-collection screen.
    const entry = byName.get(picked);
    if (entry?.tier === "demo") {
      onPickDemo(entry);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await postMintCampaign(picked);
      onMinted?.(null);
      onClose();
    } catch (e) {
      setError(IngestApiError.toOperatorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  const working = submitting || busy;

  return (
    <div className="new-campaign-body">
      <label className="new-campaign-field">
        <span>Origin</span>
        <select value={picked} onChange={(e) => setPicked(e.target.value)} required>
          {owned.map((d) => (
            <option key={d.name} value={d.name}>
              {d.title ? `${d.name} — ${d.title}` : d.name} · {d.n_samples} samples
            </option>
          ))}
          {benchmarks.length > 0 ? (
            <optgroup label="Benchmarks">
              {benchmarks.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.title ? `${d.name} — ${d.title}` : d.name} · {d.n_samples} samples
                </option>
              ))}
            </optgroup>
          ) : null}
        </select>
      </label>
      {error ? <p className="new-campaign-error">{error}</p> : null}
      <footer className="new-campaign-footer">
        <button type="button" className="new-campaign-cancel" onClick={onAddOrigin}>
          Add an Origin
        </button>
        <button
          type="button"
          className="new-campaign-submit"
          disabled={working || !picked}
          onClick={handleStart}
        >
          {working ? "Starting…" : "Start campaign"}
        </button>
      </footer>
    </div>
  );
}

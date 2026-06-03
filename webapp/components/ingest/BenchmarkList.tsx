"use client";

import { useState } from "react";
import { IngestApiError, postMintCampaign, type DatasetIndexEntry } from "@/lib/api";
import type { OnMinted } from "./types";

export function BenchmarkList({
  benchmarks,
  onClose,
  onMinted,
  onPickDemo,
  busy,
}: {
  benchmarks: DatasetIndexEntry[];
  onClose: () => void;
  onMinted?: OnMinted;
  // A demo is a dataset: picking one runs the CSV-drop path (handled by the
  // parent) instead of minting. Benchmarks still mint directly — copying a
  // full benchmark into the tenant would be wrong.
  onPickDemo: (entry: DatasetIndexEntry) => void;
  busy: boolean;
}) {
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mint = async (name: string) => {
    setSubmitting(name);
    setError(null);
    try {
      await postMintCampaign(name);
      onMinted?.(null);
      onClose();
    } catch (e) {
      setError(IngestApiError.toOperatorMessage(e));
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <>
      <ul className="new-campaign-benchmark-list">
        {benchmarks.map((d) => (
          <li key={d.name}>
            <button
              type="button"
              disabled={submitting !== null || busy}
              onClick={() => (d.tier === "demo" ? onPickDemo(d) : void mint(d.name))}
            >
              {submitting === d.name
                ? "Starting…"
                : d.title
                  ? `${d.name} — ${d.title}`
                  : d.name}
            </button>
          </li>
        ))}
      </ul>
      {error ? <p className="new-campaign-error">{error}</p> : null}
    </>
  );
}

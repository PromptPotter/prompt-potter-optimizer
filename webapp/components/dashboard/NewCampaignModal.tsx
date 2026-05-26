"use client";
// Start surface — pick a dataset, click Start. POSTs /commands/mint-campaign;
// the runner spawns in background and the new campaign id surfaces via the
// next /api/v1/active poll.

import { useEffect, useState } from "react";
import {
  fetchDatasetIndex,
  postMintCampaign,
  type DatasetIndexEntry,
} from "@/lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmitted?: () => void;
}

export function NewCampaignModal({ open, onClose, onSubmitted }: Props) {
  const [datasets, setDatasets] = useState<DatasetIndexEntry[] | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [spendCap, setSpendCap] = useState<string>("");
  const [haltAtAccuracy, setHaltAtAccuracy] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchDatasetIndex()
      .then((r) => {
        if (cancelled) return;
        setDatasets(r.datasets);
        if (r.datasets.length > 0 && !selected) setSelected(r.datasets[0].name);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [open, selected]);

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    try {
      const opts: { haltAtAccuracy?: number; spendBudgetUsd?: number } = {};
      const spend = parseFloat(spendCap);
      if (Number.isFinite(spend) && spend >= 0) opts.spendBudgetUsd = spend;
      const halt = parseFloat(haltAtAccuracy);
      if (Number.isFinite(halt) && halt >= 0 && halt <= 1) opts.haltAtAccuracy = halt;
      await postMintCampaign(selected, opts);
      onSubmitted?.();
      onClose();
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="new-campaign-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Start a new campaign"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form className="new-campaign-modal" onSubmit={handleSubmit}>
        <header className="new-campaign-header">
          <h2>Start a new campaign</h2>
          <button
            type="button"
            className="new-campaign-close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <div className="new-campaign-body">
          <label className="new-campaign-field">
            <span>Dataset</span>
            {datasets === null ? (
              <em className="new-campaign-loading">Loading…</em>
            ) : (
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                required
              >
                {datasets.length === 0 ? (
                  <option value="">(no datasets wired)</option>
                ) : null}
                {datasets.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.title ? `${d.name} — ${d.title}` : d.name}
                  </option>
                ))}
              </select>
            )}
          </label>
          <label className="new-campaign-field">
            <span>Spend cap (USD, optional)</span>
            <input
              type="number"
              min="0"
              step="0.01"
              value={spendCap}
              onChange={(e) => setSpendCap(e.target.value)}
              placeholder="e.g. 5.00"
            />
          </label>
          <label className="new-campaign-field">
            <span>Halt at accuracy (0–1, optional)</span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.01"
              value={haltAtAccuracy}
              onChange={(e) => setHaltAtAccuracy(e.target.value)}
              placeholder="e.g. 0.95"
            />
          </label>
          {error ? <p className="new-campaign-error">{error}</p> : null}
        </div>
        <footer className="new-campaign-footer">
          <button type="button" className="new-campaign-cancel" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="new-campaign-submit"
            disabled={submitting || !selected}
          >
            {submitting ? "Starting…" : "Start"}
          </button>
        </footer>
      </form>
    </div>
  );
}

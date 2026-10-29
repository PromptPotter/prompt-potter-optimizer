"use client";

import type { DatasetIndexEntry, DraftSummary, OriginEntry } from "@/lib/api";
import { fmtAgo, fmtUnit, PLACEHOLDER } from "@/lib/format";

export function DatasetPickList({
  drafts,
  origins,
  datasets,
  onReopenDraft,
  onDiscardDraft,
  onOpenOrigin,
  onPick,
  busy,
}: {
  drafts: DraftSummary[];
  origins: OriginEntry[];
  datasets: DatasetIndexEntry[];
  onReopenDraft: (draftId: string) => void;
  onDiscardDraft: (draftId: string) => void;
  onOpenOrigin: (entry: OriginEntry) => void;
  onPick: (entry: DatasetIndexEntry) => void;
  busy: boolean;
}) {
  if (drafts.length === 0 && origins.length === 0 && datasets.length === 0) {
    return (
      <div className="chat-msg ai ingest-picklist">
        <p>
          Drop a table below to start — CSV or TSV in any separator or encoding, JSON, JSONL,
          Excel or OpenDocument. Any column names work.
        </p>
      </div>
    );
  }

  const originMeta = (o: OriginEntry): string => {
    if (o.n_campaigns === 0) return "prepared";
    const acc = o.origin_accuracy == null ? "" : ` · ${fmtUnit("rate", o.origin_accuracy)}`;
    return `${o.n_campaigns} campaign${o.n_campaigns === 1 ? "" : "s"}${acc}`;
  };

  return (
    <div className="chat-msg ai ingest-picklist">
      {drafts.length > 0 ? (
        <div className="ingest-picklist-group">
          <span className="ingest-picklist-head">Open check-ins</span>
          <ul className="ingest-picklist-items ingest-picklist-scroll">
            {drafts.map((d) => (
              <li key={d.draft_id}>
                <button
                  type="button"
                  className="ingest-pick-btn"
                  disabled={busy}
                  onClick={() => onReopenDraft(d.draft_id)}
                >
                  <span className="ingest-pick-name">{d.slug}</span>
                  <span className="ingest-pick-meta">
                    {d.n_samples} · {fmtAgo(d.updated_at)}
                  </span>
                </button>
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Discard the ${d.slug} check-in`}
                  onClick={() => onDiscardDraft(d.draft_id)}
                >
                  Discard
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

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
                  disabled={busy || d.start_block !== null}
                  title="Make an origin with the check-in assistant"
                  onClick={() => onPick(d)}
                >
                  <span className="ingest-pick-name">
                    {d.title ? `${d.name} — ${d.title}` : d.name}
                  </span>
                  <span className="ingest-pick-meta">{d.n_samples ?? PLACEHOLDER}</span>
                </button>
                {d.start_block ? (
                  <p className="ingest-pick-why">
                    {d.summary ? `${d.summary} ` : ""}
                    {d.start_block.explanation}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

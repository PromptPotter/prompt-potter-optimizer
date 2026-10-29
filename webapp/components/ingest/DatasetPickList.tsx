"use client";

import {
  postCommand,
  type DatasetIndexEntry,
  type OriginEntry,
  type UnstartedCampaign,
} from "@/lib/api";
import { fmtAgo, fmtUnit, PLACEHOLDER } from "@/lib/format";
import { useCommand } from "@/lib/hooks/useCommand";
import type { CollectionState } from "@/lib/hooks/useCollection";
import { FailureLine } from "@/components/ui";

export function DatasetPickList({
  drafts,
  collection,
  onReopenDraft,
  onDiscarded,
  onOpenOrigin,
  onPick,
  busy,
}: {
  // The unstarted campaigns: each `address` is its draft id.
  drafts: UnstartedCampaign[];
  // Its own reads: the open check-ins list while these load.
  collection: CollectionState;
  onReopenDraft: (draftId: string) => void;
  onDiscarded: (draftId: string) => void;
  onOpenOrigin: (entry: OriginEntry) => void;
  onPick: (entry: DatasetIndexEntry) => void;
  busy: boolean;
}) {
  // Its spend stays banked to the account: a discard deletes the draft, never what it cost.
  const discard = useCommand<"discard">("checkin-picklist");
  const discardDraft = (draftId: string) =>
    void discard.run(
      "discard",
      () => postCommand("discard-draft", { draft_id: draftId }),
      () => onDiscarded(draftId),
    );
  const ready = collection.kind === "ready";
  const origins = ready ? collection.origins : [];
  const datasets = ready ? collection.entries : [];
  if (ready && drafts.length === 0 && origins.length === 0 && datasets.length === 0) {
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
              <li key={d.address}>
                <button
                  type="button"
                  className="ingest-pick-btn"
                  disabled={busy}
                  onClick={() => onReopenDraft(d.address)}
                >
                  <span className="ingest-pick-name">{d.label}</span>
                  <span className="ingest-pick-meta">{fmtAgo(d.span.last_at)}</span>
                </button>
                <button
                  type="button"
                  disabled={busy || discard.pending !== null}
                  aria-label={`Discard the ${d.label} check-in`}
                  onClick={() => discardDraft(d.address)}
                >
                  Discard
                </button>
              </li>
            ))}
          </ul>
          <FailureLine failure={discard.failure} />
        </div>
      ) : null}

      {collection.kind === "loading" ? (
        <p role="status" aria-live="polite">
          Loading your origins and datasets…
        </p>
      ) : null}
      {collection.kind === "error" ? <FailureLine failure={collection.failure} /> : null}

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

import { commands } from "@/lib/api";
import {
  type DatasetIndexEntry,
  type DatasetIndexResponse,
  type OriginEntry,
  type OriginListResponse,
  type UnstartedCampaign,
  START_BLOCK_WORDS,
} from "@/lib/api/types";
import { fmtAgo, fmtUnit, PLACEHOLDER } from "@/lib/format";
import { useCommand } from "@/lib/hooks/useCommand";
import type { ReadResult } from "@/lib/read-cache";
import { FailureLine } from "@/components/ui";

export function DatasetPickList({
  drafts,
  datasets: datasetsRead,
  origins: originsRead,
  onReopenDraft,
  onDiscarded,
  onOpenOrigin,
  onPick,
  busy,
}: {
  // The unstarted campaigns: each `address` is its draft id.
  drafts: readonly UnstartedCampaign[];
  // Their own reads: the open check-ins list while these load.
  datasets: ReadResult<DatasetIndexResponse>;
  origins: ReadResult<OriginListResponse>;
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
      () => commands["discard-draft"]({ draft_id: draftId }),
      () => onDiscarded(draftId),
    );
  const ready = datasetsRead.status === "ready" && originsRead.status === "ready";
  const origins = originsRead.status === "ready" ? originsRead.data.origins : [];
  const datasets = datasetsRead.status === "ready" ? datasetsRead.data.datasets : [];
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
                  aria-label={`${d.label}, edited ${fmtAgo(d.span.last_at)}`}
                  onClick={() => onReopenDraft(d.address)}
                >
                  <span className="ingest-pick-name">{d.label}</span>
                  <span className="ingest-pick-meta">{fmtAgo(d.span.last_at)}</span>
                </button>
                <button
                  type="button"
                  disabled={busy || discard.pending !== null}
                  aria-label={`Discard the ${d.label} check-in edited ${fmtAgo(d.span.last_at)}`}
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

      {!ready && datasetsRead.status !== "failed" && originsRead.status !== "failed" ? (
        <p role="status" aria-live="polite">
          Loading your origins and datasets…
        </p>
      ) : null}
      {datasetsRead.status === "failed" ? <FailureLine failure={datasetsRead.failure} /> : null}
      {originsRead.status === "failed" ? <FailureLine failure={originsRead.failure} /> : null}

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
                    {START_BLOCK_WORDS[d.start_block]}
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

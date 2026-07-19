"use client";
import { Fragment } from "react";
import { HoverCard } from "@/components/ui";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchCampaignStorage } from "@/lib/api";
import { fmtAgo, fmtBytes, fmtDateTime } from "@/lib/format";

// The ONE hover surface for every sidebar row — the card that replaced the
// clashing pair (the bespoke storage card + a native `title=` tooltip that
// floated over it). Meta is shown for every row; the on-disk breakdown only
// where a per-campaign storage endpoint exists (top-level campaign rows).
export function RowHoverCard({
  cycleId,
  description,
  datasetName,
  createdAt,
  campaignId,
  children,
}: {
  cycleId: string;
  // The one-line "what is this row" copy that used to live in `title=`.
  description: string;
  datasetName?: string | null;
  // ISO timestamp — only campaign rows carry one; the served tree has no date.
  createdAt?: string | null;
  // Present ⇒ the storage section is fetched and shown.
  campaignId?: string;
  children: React.ReactNode;
}) {
  const meta: { label: string; value: string }[] = [];
  if (datasetName) meta.push({ label: "Dataset", value: datasetName });
  if (createdAt) {
    const ago = fmtAgo(createdAt);
    meta.push({
      label: "Created",
      value: ago ? `${fmtDateTime(createdAt)} · ${ago}` : fmtDateTime(createdAt),
    });
  }
  meta.push({ label: "Cycle", value: cycleId });

  const content = (
    <div className="rowhover">
      <span className="rowhover-desc">{description}</span>
      <div className="rowhover-meta">
        {meta.map((m) => (
          <Fragment key={m.label}>
            <span className="rowhover-key">{m.label}</span>
            {/* Operator IDs stay selectable (a11y). */}
            <span className="rowhover-val">{m.value}</span>
          </Fragment>
        ))}
      </div>
      {campaignId && <CampaignStorageRows campaignId={campaignId} />}
    </div>
  );

  return <HoverCard content={content}>{children}</HoverCard>;
}

// The on-disk size breakdown, lazy-fetched while the card is open (it only
// mounts when the HoverCard is open). One MECE hierarchy: "On disk" is the
// whole; the operator axis is Dataset / Connector / Loop, and Loop = State +
// Trace + History + Reports. A note flags the keepsake `delete --keep-results`
// spares.
function CampaignStorageRows({ campaignId }: { campaignId: string }) {
  const { data, error } = useFetch((signal) => fetchCampaignStorage(campaignId, signal), [
    campaignId,
  ]);
  const fmt = (n: number | undefined) => (error ? "—" : data ? fmtBytes(n ?? 0) : "…");
  const loop = data
    ? data.state_bytes + data.trace_bytes + data.history_bytes + data.reports_bytes
    : undefined;
  const rows: { label: string; value: number | undefined; indent?: boolean }[] = [
    { label: "On disk", value: data?.on_disk_bytes },
    { label: "Dataset", value: data?.dataset_bytes },
    { label: "Connector", value: data?.connector_bytes },
    { label: "Loop", value: loop },
    { label: "State", value: data?.state_bytes, indent: true },
    { label: "Trace", value: data?.trace_bytes, indent: true },
    { label: "History", value: data?.history_bytes, indent: true },
    { label: "Reports", value: data?.reports_bytes, indent: true },
  ];
  return (
    <div className="csize-grid">
      {rows.map((r) => (
        <Fragment key={r.label}>
          <span className="csize-label" data-indent={r.indent || undefined}>
            {r.label}
          </span>
          <span className="csize-val">{fmt(r.value)}</span>
        </Fragment>
      ))}
      <span className="csize-note">delete --keep-results spares Reports + loop trace</span>
    </div>
  );
}

"use client";
import { Fragment } from "react";
import { CopyButton, HoverCard } from "@/components/ui";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchCampaignStorage } from "@/lib/api";
import { fmtAgo, fmtBytes, fmtDateTime } from "@/lib/format";

// The ONE hover surface for every sidebar row — the card that replaced the
// clashing pair (the bespoke storage card + a native `title=` tooltip that
// floated over it). Meta is shown for every row; the on-disk breakdown only
// where a per-campaign storage endpoint exists (top-level campaign rows).
//
// The card is reachable (see `HoverCard`), so what is in it selects by drag, and
// the copy button hands the same rows over as JSON. Both read the SAME two
// lists, so the payload cannot claim anything the card does not show.
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
  // Lazy either way — the card mounts `content` only while open — but owned here
  // so the copy payload carries the same numbers the grid draws.
  const { data, error } = useFetch(
    campaignId != null ? (signal) => fetchCampaignStorage(campaignId, signal) : null,
    [campaignId ?? null],
  );

  // The campaign id is what identifies this run to another person; the cycle id
  // names the run inside it. Only top-level rows have the first.
  const meta: [string, string][] = [];
  if (datasetName) meta.push(["Dataset", datasetName]);
  if (createdAt) {
    const ago = fmtAgo(createdAt);
    meta.push(["Created", ago ? `${fmtDateTime(createdAt)} · ${ago}` : fmtDateTime(createdAt)]);
  }
  if (campaignId) meta.push(["Campaign", campaignId]);
  meta.push(["Cycle", cycleId]);

  // One MECE hierarchy: "On disk" is the whole; the operator axis is Dataset /
  // Connector / Loop, and Loop = State + Trace + History + Reports. The trailing
  // flag indents a Loop leaf. Needs no campaign guard — without one there is no
  // `data`, and both consumers below gate on that.
  const loop =
    data && data.state_bytes + data.trace_bytes + data.history_bytes + data.reports_bytes;
  const sizes: [string, number | undefined, boolean?][] = [
    ["On disk", data?.on_disk_bytes],
    ["Dataset", data?.dataset_bytes],
    ["Connector", data?.connector_bytes],
    ["Loop", loop ?? undefined],
    ["State", data?.state_bytes, true],
    ["Trace", data?.trace_bytes, true],
    ["History", data?.history_bytes, true],
    ["Reports", data?.reports_bytes, true],
  ];
  const size = (n: number | undefined) => (error ? "—" : data ? fmtBytes(n ?? 0) : "…");
  const key = (label: string) => label.toLowerCase().replace(" ", "_");

  const content = (
    <div className="rowhover">
      <div className="rowhover-head">
        <span className="rowhover-desc">{description}</span>
        <CopyButton
          title="Copy these details as JSON"
          data={{
            description,
            ...Object.fromEntries(meta.map(([k, v]) => [key(k), v])),
            ...(data
              ? { on_disk: Object.fromEntries(sizes.map(([k, n]) => [key(k), size(n)])) }
              : {}),
          }}
        />
      </div>
      <div className="rowhover-meta">
        {meta.map(([label, value]) => (
          <Fragment key={label}>
            <span className="rowhover-key">{label}</span>
            {/* Operator IDs stay selectable (a11y). */}
            <span className="rowhover-val">{value}</span>
          </Fragment>
        ))}
      </div>
      {campaignId && (
        <div className="csize-grid">
          {sizes.map(([label, n, indent]) => (
            <Fragment key={label}>
              <span className="csize-label" data-indent={indent || undefined}>
                {label}
              </span>
              <span className="csize-val">{size(n)}</span>
            </Fragment>
          ))}
          {/* Flags what the keepsake `delete --keep-results` spares. */}
          <span className="csize-note">delete --keep-results spares Reports + loop trace</span>
        </div>
      )}
    </div>
  );

  return <HoverCard content={content}>{children}</HoverCard>;
}

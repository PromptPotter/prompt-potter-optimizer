"use client";
import { Fragment, useRef, useState } from "react";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchCampaignStorage } from "@/lib/api";
import { fmtBytes } from "@/lib/format";

// Hover/focus card on a campaign row showing its on-disk size. Lazy-fetches only
// while pointed at (or keyboard-focused), so the sidebar list stays cheap. The
// card is position:fixed off the row's rect so the sidebar's overflow can't clip
// it. Read-only.
//
// One MECE hierarchy: "On disk" is the whole; the operator axis is Dataset /
// Connector / Loop, and Loop = State + Trace + History + Reports (the four sum to
// Loop, all six sum to On disk). "Dataset" is the ground-truth copy; "Connector"
// is what the backend produced; the rest is the Potter's own loop work. A note
// flags the small keepsake `delete --keep-results` spares.
export function CampaignSizeHover({
  campaignId,
  children,
}: {
  campaignId: string;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const { data, error } = useFetch(
    open ? (signal) => fetchCampaignStorage(campaignId, signal) : null,
    [open, campaignId],
  );

  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setPos({ top: r.top, left: r.right + 8 });
    setOpen(true);
  };
  const hide = () => setOpen(false);

  return (
    <div
      ref={ref}
      className="csize-wrap"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {open && pos && (
        <div
          className="csize-card"
          role="tooltip"
          style={{ position: "fixed", top: pos.top, left: pos.left }}
        >
          {(() => {
            const fmt = (n: number | undefined) =>
              error ? "—" : data ? fmtBytes(n ?? 0) : "…";
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
              <>
                {rows.map((r) => (
                  <Fragment key={r.label}>
                    <span className="csize-label" data-indent={r.indent || undefined}>
                      {r.label}
                    </span>
                    <span className="csize-val">{fmt(r.value)}</span>
                  </Fragment>
                ))}
                <span className="csize-note">
                  delete --keep-results spares Reports + loop trace
                </span>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}

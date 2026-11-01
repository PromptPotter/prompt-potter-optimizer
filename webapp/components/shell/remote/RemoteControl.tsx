"use client";
import { useState } from "react";
import { FailureLine } from "@/components/ui";
import { cx } from "@/lib/cx";
import { RunControlButton } from "@/components/shell/remote/RunControlButton";
import { RemotePanel } from "./RemotePanel";
import { useRemoteModel } from "./useRemoteModel";

const SKIP_ICON = (
  <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
    <path d="M4 3.2v9.6l6-4.8z" />
    <rect x="10.5" y="3.2" width="2.2" height="9.6" rx="1" />
  </svg>
);

// Draws only the verbs the server admits now; connection state is the shell banner's alone.
export function RemoteControl() {
  const m = useRemoteModel();
  const [open, setOpen] = useState(false);
  if (!m) return null;
  const { innerHop } = m;

  return (
    <div
      className={cx("remote-control", open && "remote-control-open")}
      role="group"
      aria-label="Campaign remote control"
    >
      {open && <RemotePanel model={m} />}
      {m.inner ? (
        <button
          type="button"
          className="remote-btn remote-drill"
          onClick={m.toOuter}
          aria-label="Return to the outer campaign"
          title="Viewing an inner run's dashboard. Return to the outer campaign."
        >
          <span aria-hidden="true">↑</span>
          <span className="remote-btn-label">outer</span>
        </button>
      ) : innerHop ? (
        <button
          type="button"
          className="remote-btn remote-drill"
          onClick={() => m.toInner(innerHop.campaign_id, innerHop.cycle_id)}
          aria-label="Open the running inner cycle's dashboard"
          title={`An inner run is live — ${innerHop.campaign_id}. Open its dashboard.`}
        >
          <span aria-hidden="true">⤷</span>
          <span className="remote-btn-label">inner</span>
        </button>
      ) : null}
      <RunControlButton />
      {m.skipRefusal === "" && (
        <button
          type="button"
          className="remote-btn remote-skip"
          onClick={m.skip}
          disabled={m.pending !== null}
          aria-label="Skip the rest of this searchpoint"
          title="Cut the remaining samples of the searchpoint scoring now, accept the partial, and keep the cycle running. Marks the cycle babysat."
        >
          {SKIP_ICON}
          <span className="remote-btn-label">Skip</span>
        </button>
      )}
      <button
        type="button"
        className={cx("remote-readout", open && "remote-readout-on")}
        aria-expanded={open}
        aria-label="Job status and configuration"
        onClick={() => setOpen((v) => !v)}
      >
        <svg className="chev" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m3 4.5 3 3 3-3" />
        </svg>
      </button>
      <FailureLine failure={m.failure} className="remote-err" />
    </div>
  );
}

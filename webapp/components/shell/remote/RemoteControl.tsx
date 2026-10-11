"use client";
import { useState } from "react";
import { Term } from "@/components/ui";
import { cx } from "@/lib/cx";
import { TERMS } from "@/lib/terms";
import { fmtSigned } from "@/lib/format";
import { RunControlButton } from "@/components/shell/remote/RunControlButton";
import { RemotePanel } from "./RemotePanel";
import { useRemoteModel } from "./useRemoteModel";

const SKIP_ICON = (
  <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
    <path d="M4 3.2v9.6l6-4.8z" />
    <rect x="10.5" y="3.2" width="2.2" height="9.6" rx="1" />
  </svg>
);

export function RemoteControl() {
  const m = useRemoteModel();
  const [open, setOpen] = useState(false);
  if (!m) return null;
  const { innerHop } = m;

  return (
    <div
      className={cx(
        "remote-control",
        open && "remote-control-open",
        m.offline && "remote-control-offline",
      )}
      role="group"
      aria-label="Campaign remote control"
    >
      {open && <RemotePanel model={m} />}
      {m.inner ? (
        <button
          type="button"
          className="remote-btn remote-drill"
          onClick={m.backToOuter}
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
          onClick={() => m.drillInto(innerHop.campaignId, innerHop.cycleId)}
          aria-label="Open the running inner cycle's dashboard"
          title={`An inner run is live — ${innerHop.campaignId}. Open its dashboard.`}
        >
          <span aria-hidden="true">⤷</span>
          <span className="remote-btn-label">inner</span>
        </button>
      ) : null}
      {m.offline ? (
        <Term
          className="remote-offline"
          content="Connection to the server was lost — showing the last known state."
        >
          <span aria-hidden="true">⭘</span> reconnecting
        </Term>
      ) : null}
      <RunControlButton />
      <button
        type="button"
        className="remote-btn remote-skip"
        onClick={m.skip}
        disabled={m.skipRefusal !== "" || m.pending !== null}
        aria-label="Skip the rest of this searchpoint"
        title="Cut the remaining samples of the searchpoint scoring now, accept the partial, and keep the cycle running. Marks the cycle babysat."
      >
        {SKIP_ICON}
        <span className="remote-btn-label">Skip</span>
      </button>
      {/* Chip and chevron stay two controls: folded, reading the Term would press the toggle. */}
      <div className={cx("remote-readout", open && "remote-readout-on")}>
        <Term className="chip" content={TERMS.remote_eff}>
          <span className="chip-lbl">Lift/$</span> <strong>{fmtSigned(m.benchLiftPerUsd, 2)}</strong>
        </Term>
        <button
          type="button"
          className="remote-readout-toggle"
          aria-expanded={open}
          aria-label="Job status and configuration"
          onClick={() => setOpen((v) => !v)}
        >
          <svg className="chev" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m3 4.5 3 3 3-3" />
          </svg>
        </button>
      </div>
      {m.failure ? (
        <span className="remote-err" role="alert">
          {m.failure.message}
        </span>
      ) : null}
    </div>
  );
}

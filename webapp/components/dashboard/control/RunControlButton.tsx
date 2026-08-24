"use client";
import { useRunControl } from "@/lib/hooks/useRunControl";

const PLAY_ICON = (
  <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
    <path d="M5 3.2v9.6l8-4.8z" fill="currentColor" />
  </svg>
);

const PAUSE_ICON = (
  <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
    <rect x="4" y="3.2" width="3" height="9.6" rx="1" />
    <rect x="9" y="3.2" width="3" height="9.6" rx="1" />
  </svg>
);

// The dominant run control: a play/pause toggle beside the heat-map +
// sample-trajectory view toggles. The verb itself is `useRunControl` — shared
// with the chat composer's Tools row, so the two cannot disagree about what the
// run is doing or how to move it. This file is the icon-button form of it.
//
// Forking is NOT here: it requires a selected searchpoint. The single fork
// affordance is the Scoring inspector's "Endorse / Steer & fork" — select a
// candidate, then endorse-as-is or edit-and-steer. No blind "fork from the
// current leader" parallel write path.
export function RunControlButton({ disabledReason }: { disabledReason?: string }) {
  const run = useRunControl();
  if (!run) return null;

  // Inert form — the button mirrors the leaf's phase but fires nothing, and the
  // tooltip carries why (I3: disabled with the reason stated, never re-targeted).
  // Checked before the phase branches: an inner run's gate or check-in copy would
  // name the wrong hop's surfaces.
  if (disabledReason) {
    return (
      <div className="run-ctl" role="group" aria-label="Run control" title={disabledReason}>
        <button
          type="button"
          className={`run-ctl-primary ${run.running ? "is-pause" : "is-play"}`}
          disabled
          aria-label={`Run control unavailable — ${disabledReason}`}
        >
          {run.running ? PAUSE_ICON : PLAY_ICON}
        </button>
      </div>
    );
  }

  // Nothing this control can do from here — say which state it is, rather than
  // offering a button that misfires.
  if (run.noneReason) {
    return (
      <div className="run-ctl" role="group" aria-label="Run control">
        <span className="run-ctl-pausing" role="status">
          {run.noneReason}
        </span>
      </div>
    );
  }

  return (
    <div className="run-ctl" role="group" aria-label="Run control">
      <button
        type="button"
        className={`run-ctl-primary ${run.running ? "is-pause" : "is-play"}`}
        onClick={run.toggle}
        disabled={run.pending}
        aria-label={run.label}
        title={
          run.action === "pause"
            ? "Pause at the next round boundary"
            : run.action === "resume"
              ? "Resume the paused run"
              : "Start / resume the run"
        }
      >
        {run.running ? PAUSE_ICON : PLAY_ICON}
      </button>
      {run.pausing && run.running ? (
        <span className="run-ctl-pausing" role="status" aria-live="polite">
          {run.pausingNote}
        </span>
      ) : null}
      {run.err ? (
        <span className="run-ctl-err" role="alert">
          {run.err}
        </span>
      ) : null}
    </div>
  );
}

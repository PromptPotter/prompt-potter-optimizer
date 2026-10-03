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

// The icon-button form of `useRunControl`, shared with the chat composer. No fork here: forking
// needs a selected searchpoint (the Scoring inspector's Steer & fork).
export function RunControlButton({ disabledReason }: { disabledReason?: string }) {
  const run = useRunControl();
  if (!run) return null;

  // Checked before the phase branches: an inner run's gate copy would name the wrong hop's
  // surfaces (I3).
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

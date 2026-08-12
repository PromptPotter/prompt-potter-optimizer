"use client";
import { useState } from "react";
import { postPauseCycle, postStartRun, IngestApiError } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { phasePauseLabel, runPhaseAction } from "@/lib/run-phase";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";

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
// sample-trajectory view toggles. Play/pause action depends on phase —
// running→pause, paused→resume, else→start. The run declares `paused` even
// though it emits no telemetry while held, so the dashboard poll reads it
// directly — no separate freshness-blind probe.
//
// Forking is NOT here: it requires a selected searchpoint. The single fork
// affordance is the Scoring inspector's "Endorse / Steer & fork" — select a
// candidate, then endorse-as-is or edit-and-steer. No blind "fork from the
// current leader" parallel write path.
export function RunControlButton() {
  // `dash.run_phase` (declared by the runner, projected to dashboard.json) is
  // the run-state for the VIEWED cycle — no separate /runstate poll.
  const { dash } = useDashboard();
  const { campaignId, cycleId } = useWorkspace();
  const runPhase = dash?.run_phase;
  // What this control may do, off the TOTAL phase→action map. It used to fold every
  // phase that wasn't running/paused into "stopped" and offer Start — including a
  // warming cycle, which has no phase yet and whose Start 409s `machine_busy`.
  const action = runPhaseAction(runPhase);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // True from the moment Pause is clicked until the run actually declares
  // `paused` — the run finishes the current sample (persisting its datapoint)
  // before halting, so this window explains the wait instead of looking hung.
  const [pausing, setPausing] = useState(false);

  // Render-phase guarded reset (webapp/CLAUDE.md § State reset on prop change):
  // clear the pausing affordance the instant the run leaves "running" — it has
  // paused (or stopped/finished), so the "will pause…" message is done.
  const [prevRunPhase, setPrevRunPhase] = useState(runPhase);
  if (runPhase !== prevRunPhase) {
    setPrevRunPhase(runPhase);
    if (runPhase !== "running") setPausing(false);
  }

  if (!campaignId || !cycleId) return null;

  // Nothing this control can do from here — say which state it is, rather than
  // offering a button that misfires. At the origin gate the run is alive but
  // holding for a decision the chat thread owns; in check-in the ingest panel
  // owns Start; with no phase at all the cycle is still warming.
  if (action === "none") {
    return (
      <div className="run-ctl" role="group" aria-label="Run control">
        <span className="run-ctl-pausing" role="status">
          {runPhase === "gate"
            ? "At origin gate — decide in the chat."
            : runPhase === "checkin"
              ? "Still in check-in — start it from the setup panel."
              : "Starting up…"}
        </span>
      </div>
    );
  }

  const playing = action === "pause";

  const act = async (fn: () => Promise<unknown>) => {
    setPending(true);
    setErr(null);
    try {
      await fn();
      bumpRevalidation(); // re-tick the workspace poll (cycle list run_phase)
    } catch (e) {
      setErr(IngestApiError.toOperatorMessage(e));
    } finally {
      setPending(false);
    }
  };

  const onPlayPause = () => {
    if (action === "pause") {
      setPausing(true);
      return act(() => postPauseCycle(campaignId, cycleId));
    }
    // A paused cycle's worker has exited — resume relaunches it from the last
    // completed round (the same start-run path as a cold start), not an in-place
    // unpause. `resume` and `start` therefore take the same branch.
    return act(() => postStartRun(campaignId, cycleId, "resume"));
  };

  const label =
    action === "pause" ? "Pause run" : action === "resume" ? "Resume run" : "Start run";

  return (
    <div className="run-ctl" role="group" aria-label="Run control">
      <button
        type="button"
        className={`run-ctl-primary ${playing ? "is-pause" : "is-play"}`}
        onClick={() => void onPlayPause()}
        disabled={pending}
        aria-label={label}
        title={
          action === "pause"
            ? "Pause at the next round boundary"
            : action === "resume"
              ? "Resume the paused run"
              : "Start / resume the run"
        }
      >
        {playing ? PAUSE_ICON : PLAY_ICON}
      </button>
      {pausing && action === "pause" ? (
        <span className="run-ctl-pausing" role="status" aria-live="polite">
          Finishing {phasePauseLabel(dash?.state)} — will pause after the current sample.
        </span>
      ) : null}
      {err ? (
        <span className="run-ctl-err" role="alert">
          {err}
        </span>
      ) : null}
    </div>
  );
}

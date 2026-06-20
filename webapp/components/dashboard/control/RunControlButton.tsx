"use client";
import { useState } from "react";
import { postPauseCycle, postResumeCycle, postStartRun, IngestApiError } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { phasePauseLabel } from "@/lib/run-phase";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";

type RunPhase = "running" | "paused" | "stopped";

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
  const phase: RunPhase =
    runPhase === "paused" ? "paused" : runPhase === "running" ? "running" : "stopped";
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

  // At the origin gate the run is alive but holding for a decision the chat
  // thread owns (the inline gate decision card) — a play/pause toggle would
  // misfire (start-run on a live cycle → machine_busy). Show a non-actionable
  // status pointing at the chat instead.
  if (runPhase === "gate") {
    return (
      <div className="run-ctl" role="group" aria-label="Run control">
        <span className="run-ctl-pausing" role="status">
          At origin gate — decide in the chat.
        </span>
      </div>
    );
  }

  const playing = phase === "running";

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
    if (phase === "running") {
      setPausing(true);
      return act(() => postPauseCycle(campaignId, cycleId));
    }
    if (phase === "paused") return act(() => postResumeCycle(campaignId, cycleId));
    return act(() => postStartRun(campaignId, cycleId, "resume"));
  };

  const label =
    phase === "running" ? "Pause run" : phase === "paused" ? "Resume run" : "Start run";

  return (
    <div className="run-ctl" role="group" aria-label="Run control">
      <button
        type="button"
        className={`run-ctl-primary ${playing ? "is-pause" : "is-play"}`}
        onClick={() => void onPlayPause()}
        disabled={pending}
        aria-label={label}
        title={
          phase === "running"
            ? "Pause at the next round boundary"
            : phase === "paused"
              ? "Resume the paused run"
              : "Start / resume the run"
        }
      >
        {playing ? PAUSE_ICON : PLAY_ICON}
      </button>
      {pausing && phase === "running" ? (
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

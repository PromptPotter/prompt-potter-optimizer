"use client";
import { useState } from "react";
import { postPauseCycle, postStartRun, IngestApiError } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { phasePauseLabel, runPhaseAction, type RunAction } from "@/lib/run-phase";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";

// Start / pause the viewed cycle — the VERB, with no opinion about what it looks
// like. Two surfaces drive it: the dashboard's play/pause button and the chat
// composer's Tools row ("Optimize prompt while using"), which is the same run
// stated in the reader's words rather than a second mechanism.
//
// `run_phase` is declared by the runner and projected to dashboard.json, so the
// state comes off the ordinary poll — no separate probe.
export interface RunControl {
  action: RunAction;
  // The run is going right now, i.e. the toggle reads as ON.
  running: boolean;
  // A command is in flight.
  pending: boolean;
  // Pause is clicked but the run has not declared `paused` yet — it finishes the
  // current sample (persisting its datapoint) first, and this window explains the
  // wait instead of looking hung.
  pausing: boolean;
  pausingNote: string;
  err: string | null;
  label: string;
  // Set when `action` is "none": the run is alive but this control is not the one
  // that may move it. Stated, never rendered as a dead button (§ I3).
  noneReason: string | null;
  toggle: () => void;
}

// `null` when no cycle is bound — there is nothing to start or pause.
export function useRunControl(): RunControl | null {
  const { dash } = useDashboard();
  const { campaignId, cycleId } = useWorkspace();
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [pausing, setPausing] = useState(false);

  const runPhase = dash?.run_phase;
  const action = runPhaseAction(runPhase);

  // Render-phase guarded reset (webapp/CLAUDE.md § State reset on prop change):
  // clear the pausing affordance the instant the run leaves "running" — it has
  // paused (or stopped/finished), so the "will pause…" message is done.
  const [prevRunPhase, setPrevRunPhase] = useState(runPhase);
  if (runPhase !== prevRunPhase) {
    setPrevRunPhase(runPhase);
    if (runPhase !== "running") setPausing(false);
  }

  if (!campaignId || !cycleId) return null;

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

  const toggle = () => {
    if (action === "none") return;
    if (action === "pause") {
      setPausing(true);
      void act(() => postPauseCycle(campaignId, cycleId));
      return;
    }
    // A paused cycle's worker has exited — resume relaunches it from the last
    // completed round (the same start-run path as a cold start), not an in-place
    // unpause. `resume` and `start` therefore take the same branch.
    void act(() => postStartRun(campaignId, cycleId, "resume"));
  };

  return {
    action,
    running: action === "pause",
    pending,
    pausing,
    pausingNote: `Finishing ${phasePauseLabel(dash?.state)} — will pause after the current sample.`,
    err,
    label: action === "pause" ? "Pause run" : action === "resume" ? "Resume run" : "Start run",
    // At the origin gate the run is alive but holding for a decision the chat
    // thread owns; in check-in the ingest panel owns Start; with no phase at all
    // the cycle is still warming.
    noneReason:
      action !== "none"
        ? null
        : runPhase === "gate"
          ? "At origin gate — decide in the chat."
          : runPhase === "checkin"
            ? "Still in check-in — start it from the setup panel."
            : "Starting up…",
    toggle,
  };
}

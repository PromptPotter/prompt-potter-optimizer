"use client";
import { useState } from "react";
import { postPauseCycle, postStartRun } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import { phasePauseLabel, runPhaseAction, type RunAction } from "@/lib/run-phase";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";

// Start / pause the viewed cycle — the VERB, with no opinion about what it looks like; every
// run toggle rides it.
export interface RunControl {
  action: RunAction;
  running: boolean;
  pending: boolean;
  // Pause clicked, `paused` not yet declared: the runner finishes the current sample first.
  pausing: boolean;
  pausingNote: string;
  err: string | null;
  label: string;
  // Stated, never rendered as a dead button (I3).
  noneReason: string | null;
  toggle: () => void;
}

export function useRunControl(): RunControl | null {
  const { dash } = useDashboard();
  const { campaignId, cycleId } = useWorkspace();
  const cmd = useCommand<"pause-cycle" | "start-run">("run-control");
  const [pausing, setPausing] = useState(false);

  const runPhase = dash?.run_phase;
  const action = runPhaseAction(runPhase);

  const [prevRunPhase, setPrevRunPhase] = useState(runPhase);
  if (runPhase !== prevRunPhase) {
    setPrevRunPhase(runPhase);
    if (runPhase !== "running") setPausing(false);
  }

  if (!campaignId || !cycleId) return null;

  const toggle = () => {
    if (action === "none") return;
    if (action === "pause") {
      setPausing(true);
      void cmd.run("pause-cycle", () => postPauseCycle(campaignId, cycleId));
      return;
    }
    // A paused cycle's worker has exited, so resume is a relaunch: the same branch as start.
    void cmd.run("start-run", () => postStartRun(campaignId, cycleId, "resume"));
  };

  return {
    action,
    running: action === "pause",
    pending: cmd.pending !== null,
    // A refused pause retires the note, or it promises a wait that never ends.
    pausing: pausing && cmd.failure === null,
    pausingNote: `Finishing ${phasePauseLabel(dash?.state)} — will pause after the current sample.`,
    err: cmd.failure?.message ?? null,
    label: action === "pause" ? "Pause run" : action === "resume" ? "Resume run" : "Start run",
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

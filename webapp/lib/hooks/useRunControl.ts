"use client";
import { useState } from "react";
import { postPauseCycle, postStartRun } from "@/lib/api";
import { useCommand } from "@/lib/hooks/useCommand";
import type { RunAdmission } from "@/lib/api/types";
import { phasePauseLabel, phaseWalks } from "@/lib/run-phase";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";

export interface RunControl {
  action: RunAdmission["offers"];
  running: boolean;
  pending: boolean;
  // Pause clicked, `paused` not yet declared: the runner finishes the current sample first.
  pausing: boolean;
  pausingNote: string;
  err: string | null;
  label: string;
  noneReason: string | null;
  // served: `dash.pause`; null in every other phase.
  pauseNote: string | null;
  toggle: () => void;
}

export function useRunControl(): RunControl | null {
  const { dash } = useCycleStream();
  const { viewedPath } = useWorkspace();
  const cmd = useCommand<"pause-cycle" | "start-run">("run-control");
  const [pausing, setPausing] = useState(false);

  const runPhase = dash?.run_phase;
  const admission = dash?.run_admission;
  const action = admission?.offers ?? null;
  const pause = dash?.pause ?? null;

  const [prevRunPhase, setPrevRunPhase] = useState(runPhase);
  if (runPhase !== prevRunPhase) {
    setPrevRunPhase(runPhase);
    if (!phaseWalks(runPhase)) setPausing(false);
  }

  if (!viewedPath) return null;

  const toggle = () => {
    if (action === null) return;
    if (action === "pause") {
      setPausing(true);
      void cmd.run("pause-cycle", () => postPauseCycle(viewedPath));
      return;
    }
    // A paused cycle's worker has exited, so resume is a relaunch: the same branch as start.
    void cmd.run("start-run", () => postStartRun(viewedPath));
  };

  return {
    action,
    running: action === "pause",
    pending: cmd.pending !== null,
    // A refused pause retires the note, or it promises a wait that never ends.
    pausing: pausing && cmd.failure === null,
    pausingNote: `Finishing ${phasePauseLabel(dash?.state, dash?.optimizer_step)} — will pause after the current sample.`,
    err: cmd.failure?.message ?? null,
    label: action === "pause" ? "Pause run" : action === "resume" ? "Resume run" : "Start run",
    // No dashboard yet: a warming cycle's launch is already in flight.
    noneReason: admission === undefined ? "Starting up…" : admission.refusal || null,
    pauseNote: pause
      ? `Paused by ${pause.cause}${pause.detail ? ` (${pause.detail})` : ""} — ${pause.next_step}`
      : null,
    toggle,
  };
}

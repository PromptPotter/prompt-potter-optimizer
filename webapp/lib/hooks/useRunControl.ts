import { useState } from "react";
import { commands, cycleAddress } from "@/lib/api";
import { useCommand, type CommandFailure } from "@/lib/hooks/useCommand";
import type { RunAdmission } from "@/lib/api/types";
import { pauseNextStep, phasePauseLabel, phaseWalks } from "@/lib/derivations";
import { useViewedDashboard } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";

interface RunControl {
  action: RunAdmission["offers"];
  running: boolean;
  pending: boolean;
  // Pause clicked, `paused` not yet declared: the runner finishes the current sample first.
  pausing: boolean;
  pausingNote: string;
  failure: CommandFailure | null;
  label: string;
  noneReason: string | null;
  // served: `dash.pause`; null in every other phase.
  pauseNote: string | null;
  toggle: () => void;
  // Launch the cycle for *rounds* closed rounds, then pause it as `stepped`; null while it runs.
  step: ((rounds: number) => void) | null;
}

export function useRunControl(): RunControl | null {
  const { dash } = useViewedDashboard();
  const { viewedPath } = useWorkspace();
  const cmd = useCommand<"pause" | "start-run">("run-control");
  const [pausing, setPausing] = useState(false);

  const runPhase = dash?.run_phase;
  const admission = dash?.admission;
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
      void cmd.run("pause", () => commands["pause"](cycleAddress(viewedPath)));
      return;
    }
    // A paused cycle's worker has exited, so resume is a relaunch: the same branch as start.
    void cmd.run("start-run", () => commands["start-run"](cycleAddress(viewedPath)));
  };

  const step = (rounds: number) =>
    void cmd.run("start-run", () =>
      commands["start-run"]({ ...cycleAddress(viewedPath), step_rounds: rounds }),
    );

  return {
    action,
    running: action === "pause",
    pending: cmd.pending !== null,
    // A refused pause retires the note, or it promises a wait that never ends.
    pausing: pausing && cmd.failure === null,
    pausingNote: dash
      ? `Finishing ${phasePauseLabel(dash.state, dash.optimizer_step)} — will pause after the current sample.`
      : "",
    failure: cmd.failure,
    label: action === "pause" ? "Pause run" : action === "resume" ? "Resume run" : "Start run",
    // No dashboard yet: a warming cycle's launch is already in flight.
    noneReason: admission === undefined ? "Starting up…" : admission.refusal || null,
    pauseNote: pause
      ? `Paused by ${pause.cause}${pause.detail ? ` (${pause.detail})` : ""} — ${pauseNextStep(pause)}`
      : null,
    toggle,
    step: action === null || action === "pause" ? null : step,
  };
}

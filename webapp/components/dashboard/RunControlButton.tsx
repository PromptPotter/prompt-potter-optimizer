"use client";
import { useState } from "react";
import {
  postPauseCycle,
  postResumeCycle,
  postStartRun,
  postCreateFork,
  IngestApiError,
} from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { Modal } from "@/components/shell/Modal";
import { roundOf, type DashboardSnapshot } from "@/lib/poll";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  // `dash.run_phase` (declared by the runner, projected to dashboard.json) is
  // the run-state for the VIEWED cycle — no separate /runstate poll. `dash` also
  // supplies the fork point (the live round's PoBB leader).
  dash: DashboardSnapshot | null;
}

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

const FORK_ICON = (
  <svg
    viewBox="0 0 16 16"
    width="13"
    height="13"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <circle cx="4.5" cy="3.5" r="1.6" />
    <circle cx="4.5" cy="12.5" r="1.6" />
    <circle cx="11.5" cy="6.5" r="1.6" />
    <path d="M4.5 5.1v6M4.5 9c0-2 .5-3.5 3-3.5h1.5" />
  </svg>
);

// The dominant run control: a play/pause toggle plus a conditional fork,
// placed beside the heat-map + sample-trajectory view toggles. Play/pause
// action depends on phase — running→pause, paused→resume, else→start. The run
// declares `paused` even though it emits no telemetry while held, so the
// dashboard poll reads it directly — no separate freshness-blind probe.
export function RunControlButton({ campaignId, cycleId, dash }: Props) {
  const runPhase = dash?.run_phase;
  const phase: RunPhase =
    runPhase === "paused" ? "paused" : runPhase === "running" ? "running" : "stopped";
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmFork, setConfirmFork] = useState(false);

  // Fork point = the live round's PoBB leader. Only a live round exposes a
  // determinable candidate, so fork is "allowed" only while one is running.
  const pobb = dash?.current_round?.pobb;
  const forkRound = roundOf(dash);
  const forkCandidate = pobb?.current_id ?? pobb?.top?.[0]?.id ?? null;

  if (!campaignId || !cycleId) return null;

  const canFork = forkRound != null && !!forkCandidate;
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
    if (phase === "running") return act(() => postPauseCycle(campaignId, cycleId));
    if (phase === "paused") return act(() => postResumeCycle(campaignId, cycleId));
    return act(() => postStartRun(campaignId, cycleId, "resume"));
  };

  const doFork = () => {
    setConfirmFork(false);
    if (forkRound == null || !forkCandidate) return;
    void act(() => postCreateFork(campaignId, cycleId, forkRound, forkCandidate));
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
      {canFork ? (
        <button
          type="button"
          className="run-ctl-fork"
          onClick={() => setConfirmFork(true)}
          disabled={pending}
          aria-label="Fork from current best"
          title={`Fork a sibling from R${forkRound}.${forkCandidate} (current leader)`}
        >
          {FORK_ICON}
        </button>
      ) : null}
      {err ? (
        <span className="run-ctl-err" role="alert">
          {err}
        </span>
      ) : null}
      <Modal
        open={confirmFork}
        title="Fork from the current best?"
        message={`Mints a sibling cycle rooted at R${forkRound}.${forkCandidate} (the current leader). The active pointer retargets to the fork; the parent keeps running.`}
        actions={[
          { label: "Cancel", onClick: () => setConfirmFork(false) },
          { label: "Fork", variant: "primary", onClick: doFork },
        ]}
        onClose={() => setConfirmFork(false)}
      />
    </div>
  );
}

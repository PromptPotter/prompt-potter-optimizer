"use client";
import { useState } from "react";
import { postSkipSearchpoint, IngestApiError } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { runPhaseLabel } from "@/lib/run-phase";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { RunControlButton } from "@/components/dashboard/control/RunControlButton";

// The global remote control — a bottom-fixed hovering pill, rendered as shell
// chrome on every tab while a cycle is live. It consolidates the run controls
// that were scattered (play/pause was buried in the Chat-tab heat-map): one
// strip with run-phase, play/pause (the reused RunControlButton), Skip, and
// concise round/spend status, plus the babysat tag once the operator has
// intervened. Pause is the single interrupt verb — there is no separate Stop;
// pausing exits the worker cleanly and the play button resumes from the last
// completed round.
//
// `Skip` (skip-searchpoint) is the one net-new control: it cuts the remaining
// samples of the searchpoint scoring now, accepts the partial, and the cycle
// continues — and marks the cycle human_intervened. Enabled only while running
// (skipping only means something mid-scoring).

const SKIP_ICON = (
  <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
    <path d="M4 3.2v9.6l6-4.8z" />
    <rect x="10.5" y="3.2" width="2.2" height="9.6" rx="1" />
  </svg>
);

export function RemoteBar() {
  // Identity from the workspace; live state from the per-cycle dashboard stream.
  const { campaignId, cycleId, cycles } = useWorkspace();
  const { dash, dashRound, runPhaseResolved } = useDashboard();
  const [pending, setPending] = useState<"skip" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (!campaignId || !cycleId) return null;

  const runPhase = runPhaseResolved;
  // Present only while the cycle is alive — running / paused (so you can resume)
  // / origin gate. Hidden when terminal, detached, or no run yet.
  const active = runPhase === "running" || runPhase === "paused" || runPhase === "gate";
  if (!active) return null;

  // Babysat marker for the in-view cycle — the canonical flag rides the cycle
  // list (index.json::human_intervened), permanent once an operator intervenes.
  const babysat = Boolean(
    cycles.find((c) => c.campaign_id === campaignId && c.cycle_id === cycleId)?.human_intervened,
  );
  const spend = dash?.spend;

  const act = async (which: "skip", fn: () => Promise<unknown>) => {
    setPending(which);
    setErr(null);
    try {
      await fn();
      bumpRevalidation(); // re-tick the workspace poll (run_phase + babysat flag)
    } catch (e) {
      setErr(IngestApiError.toOperatorMessage(e));
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="remote-bar" role="group" aria-label="Campaign remote control">
      <span className={`remote-phase remote-phase-${runPhase}`}>
        <span className="remote-dot" aria-hidden="true" />
        {runPhaseLabel(runPhase, dash?.stop_reason)}
      </span>
      <RunControlButton />
      <button
        type="button"
        className="remote-btn remote-skip"
        onClick={() => void act("skip", () => postSkipSearchpoint(campaignId, cycleId))}
        disabled={runPhase !== "running" || pending !== null}
        aria-label="Skip the rest of this searchpoint"
        title="Cut the remaining samples of the searchpoint scoring now, accept the partial, and keep the cycle running. Marks the cycle babysat."
      >
        {SKIP_ICON}
        <span className="remote-btn-label">Skip</span>
      </button>
      <span className="remote-status" aria-live="off">
        {dashRound != null ? <span className="remote-round">R{dashRound}</span> : null}
        {spend ? (
          <span className="remote-spend">
            ${spend.total_used_usd.toFixed(2)}
            {spend.budget_usd != null ? ` / $${spend.budget_usd.toFixed(2)}` : ""}
          </span>
        ) : null}
        {spend && spend.backend.unpriced_tokens + spend.loop.unpriced_tokens > 0 ? (
          <span
            className="remote-spend-warn"
            title="USD cost couldn't be resolved for some calls (e.g. Groq returns no wire cost and the model isn't in the rate table). The $ figure undercounts real spend and the USD cap can't see it — the token cap is the backstop."
          >
            <span aria-hidden="true">⚠</span> USD cap inactive
          </span>
        ) : null}
      </span>
      {babysat ? (
        <span
          className="remote-babysat"
          title="An operator manually intervened (skip) — this cycle is no longer purely reproducible."
        >
          <span aria-hidden="true">✎</span> babysat
        </span>
      ) : null}
      {err ? (
        <span className="remote-err" role="alert">
          {err}
        </span>
      ) : null}
    </div>
  );
}

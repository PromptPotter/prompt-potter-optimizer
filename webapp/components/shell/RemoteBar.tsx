"use client";
import { useState } from "react";
import { postSkipSearchpoint, IngestApiError } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { runPhaseLabel, isInFlight } from "@/lib/run-phase";
import { cx } from "@/lib/cx";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { RunControlButton } from "@/components/dashboard/control/RunControlButton";
import { activeNodeId } from "@/components/workflow";

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
//
// The run-phase chip doubles as the follow-active control while the view is
// PINNED. While following it stays a plain span — `followActive()` is a no-op
// there, and a button that does nothing is a lie (surface-contract I3).

const SKIP_ICON = (
  <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
    <path d="M4 3.2v9.6l6-4.8z" />
    <rect x="10.5" y="3.2" width="2.2" height="9.6" rx="1" />
  </svg>
);

interface Props {
  // Called after the operator follows the active run, so the shell can switch to
  // the Dashboard view — same contract as RunningJobsButton's `onPicked`.
  onFollowed?: () => void;
}

export function RemoteBar({ onFollowed }: Props) {
  // Identity from the workspace; live state from the per-cycle dashboard stream.
  const { campaignId, cycleId, cycles, following, followActive } = useWorkspace();
  const { dash, dashRound, status } = useDashboard();
  const [pending, setPending] = useState<"skip" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (!campaignId || !cycleId) return null;

  // The server-declared phase, never a client connection guess — the same
  // in-flight set the navbar counts (running / gate / paused). A poll blip
  // no longer unmounts this bar (frontend-surface-contract I6); connection
  // loss is shown instead by dimming it (`offline` below), reusing the poll's
  // existing staleness signal rather than a second liveness channel.
  const runPhase = dash?.run_phase ?? null;
  // Hidden only when terminal, checkin, or no run yet.
  if (!isInFlight(runPhase)) return null;
  const offline = status === "offline";

  // Hoisted so the chip's two forms render byte-identical contents.
  const phaseLabel = runPhaseLabel(runPhase, dash?.stop_reason);
  const phase = (
    <>
      <span className="phase-dot" aria-hidden="true" />
      {phaseLabel}
    </>
  );

  // Babysat marker for the in-view cycle — the canonical flag rides the cycle
  // list (index.json::human_intervened), permanent once an operator intervenes.
  const babysat = Boolean(
    cycles.find((c) => c.campaign_id === campaignId && c.cycle_id === cycleId)?.human_intervened,
  );
  const spend = dash?.spend;
  // The armed USD ceiling comes from run_limits — the single authoritative
  // budget source (same field ChatPane's readSpend reads), never the retired
  // spend.budget_usd, so the remote pill and the job-bar can't disagree.
  const budgetUsd =
    typeof dash?.run_limits?.spend_budget_usd === "number"
      ? dash.run_limits.spend_budget_usd
      : null;
  // The candidate currently being scored ("C3.2"). `dash.candidate` is "C3.2/4"
  // and goes stale between rounds, so surface it only while the active node is
  // the scorer — that's the window where it's the live position. This is the
  // finer "where am I" the remote was missing: round AND candidate.
  const scoringCand =
    activeNodeId(dash?.in_flight?.node ?? null, dash?.state) === "l1_score"
      ? String(dash?.candidate || "").split("/")[0]
      : "";

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
    <div
      className={cx("remote-bar", offline && "remote-bar-offline")}
      role="group"
      aria-label="Campaign remote control"
    >
      {following ? (
        <span className={cx("phase-chip", `phase-${runPhase}`)}>{phase}</span>
      ) : (
        <button
          type="button"
          className={cx("phase-chip", "remote-follow", `phase-${runPhase}`)}
          onClick={() => {
            followActive();
            onFollowed?.();
          }}
          aria-label={`${phaseLabel} — pinned to this campaign. Follow the campaign the CLI is currently running.`}
        >
          {phase}
          {/* The breadcrumb's own button, floated above the pill. The tag IS the
              tooltip, so no `title` on top of it. */}
          <span className="follow-active-btn" aria-hidden="true">
            ↪ Follow active
          </span>
        </button>
      )}
      {offline ? (
        <span
          className="remote-offline"
          title="Connection to the server was lost — showing the last known state."
        >
          <span aria-hidden="true">⭘</span> reconnecting
        </span>
      ) : null}
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
        {/* The candidate label ("C2.3") already encodes the round (the 2), so
            show it INSTEAD of "R{n}" while scoring — round only when there's no
            candidate (between rounds / generating). */}
        {scoringCand ? (
          <span className="remote-cand">{scoringCand}</span>
        ) : dashRound != null ? (
          <span className="remote-round">R{dashRound}</span>
        ) : null}
        {spend ? (
          <span className="remote-spend">
            ${spend.total_used_usd.toFixed(2)}
            {budgetUsd != null ? ` / $${budgetUsd.toFixed(2)}` : ""}
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

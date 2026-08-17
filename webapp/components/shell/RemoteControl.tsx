"use client";
import { useState } from "react";
import { postSkipSearchpoint, postSetSampleLookahead, IngestApiError } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { runPhaseLabel, isInFlight } from "@/lib/run-phase";
import { cx } from "@/lib/cx";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { RunControlButton } from "@/components/dashboard/control/RunControlButton";

// The global remote control — bottom-fixed and hovering, rendered as shell
// chrome on every tab while a cycle is live. The name is the one the operator
// hears (`aria-label` below); it is not named for its shape, because "pill" in
// this codebase means a border-radius and already belongs to four other things
// (Badge, the round-axis LIVE marker, the provenance tag, the round strip).
// It consolidates the run controls
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
  // the Dashboard view — same contract as the JobsDock's `onPicked`.
  onFollowed?: () => void;
}

export function RemoteControl({ onFollowed }: Props) {
  // Identity from the workspace; live state from the per-cycle dashboard stream.
  const {
    campaignId,
    cycleId,
    leafCampaignId,
    leafCycleId,
    viewedPath,
    cycles,
    following,
    followActive,
  } = useWorkspace();
  const { dash, dashRound, status } = useDashboard();
  const [pending, setPending] = useState<"skip" | "sample-lookahead" | null>(null);
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

  // The phase above is the LEAF's; the command highway addresses exactly one hop
  // and has no `descend`, so an inner cycle cannot be commanded. Firing anyway
  // sent Skip/Pause — enabled by the INNER run's `running` — at the outer cycle.
  // Disabled with the reason stated, rather than re-targeted (I3_affordance_honest).
  const inner = (viewedPath?.length ?? 1) > 1;
  const innerReason = inner
    ? "Run control reaches the outer campaign only — back out of this inner run to use it."
    : undefined;

  // Babysat marker — the canonical flag rides the cycle list
  // (index.json::human_intervened), permanent once an operator intervenes. Matched
  // on the hop the CHIP describes, so it can't advertise the outer cycle's history
  // beside an inner run's phase; an inner cycle is absent from `/cycles`, so it
  // simply doesn't claim one.
  const babysat = Boolean(
    cycles.find((c) => c.campaign_id === leafCampaignId && c.cycle_id === leafCycleId)
      ?.human_intervened,
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
    dash?.current_round.active_node === "l1_score"
      ? String(dash?.candidate || "").split("/")[0]
      : "";

  // SERVER state, never a local toggle (I6): the arming expires on its own, so a client-held
  // boolean would stay lit after the round spent it.
  const lookahead = dash?.sample_lookahead ?? 1;
  const sampleLookaheadArmed = lookahead > 1;
  const discards = dash?.sample_lookahead_discards ?? 0;

  const act = async (which: "skip" | "sample-lookahead", fn: () => Promise<unknown>) => {
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
      className={cx("remote-control", offline && "remote-control-offline")}
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
      {inner ? (
        <span className="remote-inner-note" role="status">
          {innerReason}
        </span>
      ) : (
        <RunControlButton />
      )}
      <button
        type="button"
        className="remote-btn remote-skip"
        onClick={() => void act("skip", () => postSkipSearchpoint(campaignId, cycleId))}
        disabled={inner || runPhase !== "running" || pending !== null}
        aria-label="Skip the rest of this searchpoint"
        title={
          innerReason ??
          "Cut the remaining samples of the searchpoint scoring now, accept the partial, and keep the cycle running. Marks the cycle babysat."
        }
      >
        {SKIP_ICON}
        <span className="remote-btn-label">Skip</span>
      </button>
      {/* An ARM button, not a switch: it spends itself after one round of scoring. */}
      <button
        type="button"
        className={cx("remote-btn", "remote-sample-lookahead", sampleLookaheadArmed && "armed")}
        onClick={() =>
          void act("sample-lookahead", () =>
            postSetSampleLookahead(campaignId, cycleId, !sampleLookaheadArmed),
          )
        }
        disabled={inner || runPhase !== "running" || pending !== null}
        aria-pressed={sampleLookaheadArmed}
        aria-label={
          sampleLookaheadArmed
            ? "Cancel sample look-ahead (currently 2 samples in flight)"
            : "Arm sample look-ahead for the next round of scoring"
        }
        title={
          innerReason ??
          (sampleLookaheadArmed
            ? `Look-ahead armed — 2 samples in flight, about half the wall clock. Expires when this round finishes scoring. Costs at most one discarded backend call per eliminated candidate${discards > 0 ? ` (${discards} so far)` : ""}; the measurement is unchanged and the cycle is NOT marked babysat.`
            : "Run the next round's scoring with 2 samples in flight instead of 1 — roughly half the wall clock. Expires after that one round. The measurement is unchanged.")
        }
      >
        <span aria-hidden="true">⇉</span>
        <span className="remote-btn-label">{sampleLookaheadArmed ? `${lookahead}×` : "Look-ahead"}</span>
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

"use client";
import { useMemo, useState } from "react";
import { postSkipSearchpoint, postSetSampleLookahead, IngestApiError } from "@/lib/api";
import { SegmentedControl } from "@/components/ui";
import { bumpRevalidation } from "@/lib/revalidate";
import { runPhaseLabel } from "@/lib/run-phase";
import { cx } from "@/lib/cx";
import { TERMS } from "@/lib/terms";
import { headlineStats, pathOf, readSpend, runningInnerRun } from "@/lib/derivations";
import { fmtText, fmtDuration, fmtUsd, fmtTokens, fmtPct0 } from "@/lib/format";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useLineageTree } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";
import { RunControlButton } from "@/components/dashboard/control/RunControlButton";
import { SpendBudgetControl } from "@/components/dashboard/control/SpendBudgetControl";

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

// ETA to budget — burn rate = used / cycle_age, ETA = remaining_budget / burn. A plain
// module-level helper (not a component/hook) so the wallclock read is allowed; returns "—"
// until spend is wired or when the budget is uncapped. Meaningless once a run is TERMINAL,
// where the caller shows the stop reason instead: there is no remaining work to project.
function etaToBudget(
  usedUsd: number | null,
  budgetUsd: number | null,
  cycleStartedAt: string | null,
): string {
  if (usedUsd == null || budgetUsd == null || !cycleStartedAt) return "—";
  const startedMs = Date.parse(cycleStartedAt);
  if (!Number.isFinite(startedMs)) return "—";
  const ageSec = (Date.now() - startedMs) / 1000;
  if (ageSec <= 0 || usedUsd <= 0) return "—";
  if (usedUsd >= budgetUsd) return "spent";
  const burn = usedUsd / ageSec; // $/sec
  const remainingSec = (budgetUsd - usedUsd) / burn;
  return fmtDuration(remainingSec);
}

interface Props {
  // Called after the operator follows the active run, so the shell can switch to
  // the Dashboard view — same contract as the JobsDock's `onPicked`.
  onFollowed?: () => void;
  // The viewed cycle's creation stamp, for the burn-rate ETA. Shell-owned because the
  // leaf hop's own stamp is what the strip describes.
  cycleStartedAt?: string | null;
}

export function RemoteControl({ onFollowed, cycleStartedAt = null }: Props) {
  // Identity from the workspace; live state from the per-cycle dashboard stream.
  const {
    campaignId,
    cycleId,
    leafCampaignId,
    leafCycleId,
    leafIsL4,
    viewedPath,
    cycles,
    following,
    followActive,
    drillInto,
    backToOuter,
  } = useWorkspace();
  const { dash, dashRound, status } = useDashboard();
  const [pending, setPending] = useState<"skip" | "sample-lookahead" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  // The strip's one lineage read, only to offer the drill into a RUNNING inner
  // cycle while the OUTER of a self-optimizing campaign is viewed. Rides the
  // campaign's single tree entry (lib/lineage store) — no second fetch.
  const isOuterView = (viewedPath?.length ?? 1) === 1;
  const { root: lineageRoot } = useLineageTree(
    viewedPath ?? [],
    Boolean(viewedPath) && isOuterView && leafIsL4,
  );
  const innerRun = useMemo(() => runningInnerRun(lineageRoot), [lineageRoot]);

  if (!campaignId || !cycleId) return null;

  // The server-declared phase, never a client connection guess. A poll blip no longer
  // unmounts this bar (frontend-surface-contract I6); connection loss is shown instead
  // by dimming it (`offline` below), reusing the poll's existing staleness signal rather
  // than a second liveness channel.
  const runPhase = dash?.run_phase ?? null;
  // Hidden only for check-in (the ingest panel owns Start there) and a cycle with no phase
  // yet. It deliberately SURVIVES `terminal` and `detached`: `run-phase.ts::PHASE_ACTION`
  // maps both to "start", and this is `RunControlButton`'s only mount — gating on
  // `isInFlight` left both of those arms declared and unreachable, so a budget-halted or
  // crashed cycle simply lost its play button. Those are also the two states where the
  // readout below matters most: what the run got, and what it cost.
  if (runPhase === null || runPhase === "checkin") return null;
  const terminal = runPhase === "terminal";
  const offline = status === "offline";

  // Hoisted so the chip's two forms render byte-identical contents.
  const phaseLabel = runPhaseLabel(runPhase, dash?.stop_reason);
  const phase = (
    <>
      <span className="phase-dot" aria-hidden="true" />
      {phaseLabel}
    </>
  );

  // The phase above is the LEAF's, and Skip/Pause address the ROOT hop, so firing them
  // from an inner view sent a command the inner run's `running` had enabled at the outer
  // cycle. Disabled with the reason as the tooltip, rather than re-targeted
  // (I3_affordance_honest). Concurrency is the exception and descends — see below.
  const inner = !isOuterView;
  const innerReason = inner
    ? "Run control reaches the outer campaign only — back out of this inner run to use it."
    : undefined;
  // The drill toggle's target while the outer is viewed; `pathOf` because an inner
  // cycle_id repeats across sandboxes — the path is the address.
  const innerHop = innerRun ? pathOf(innerRun).at(-1) : undefined;

  // Babysat marker — the canonical flag rides the cycle list
  // (index.json::human_intervened), permanent once an operator intervenes. Matched
  // on the hop the CHIP describes, so it can't advertise the outer cycle's history
  // beside an inner run's phase; an inner cycle is absent from `/cycles`, so it
  // simply doesn't claim one.
  const leafEntry = cycles.find(
    (c) => c.campaign_id === leafCampaignId && c.cycle_id === leafCycleId,
  );
  const babysat = Boolean(leafEntry?.human_intervened);
  const spend = dash?.spend;
  // One parser for the whole block, ceilings included — `readSpend` already reads the armed
  // `run_limits` pair (the gate's own source). This bar used to re-spell the USD ceiling
  // inline, which is the second spelling that lets two surfaces disagree.
  const {
    backendUsd,
    loopUsd,
    usedUsd,
    budgetUsd,
    budgetTokens,
    rateKnown,
    backendTokens,
    loopTokens,
    totalTokens,
    unpricedTokens,
  } = readSpend(dash);

  // Headline KPIs. `abilityDelta` is SERVED and is in LOGITS, so it renders as θ and never as
  // a percent — the two are different bases, not different renderings. Lift over origin leads
  // because it is the meaningful number; absolute best rides as secondary context.
  const { best, abilityDelta } = headlineStats(dash);
  const deltaTheta =
    abilityDelta != null ? `θ ${abilityDelta >= 0 ? "+" : ""}${abilityDelta.toFixed(2)}` : "—";
  const deltaPerSpend =
    abilityDelta != null && usedUsd != null && usedUsd > 0 ? abilityDelta / usedUsd : null;
  const effChip = deltaPerSpend != null ? `${deltaPerSpend.toFixed(2)} θ/$` : "—";
  // A finished run has no remaining work to project, so the third slot carries WHY it stopped
  // instead of a duration nobody can act on.
  const etaChip = terminal
    ? runPhaseLabel(runPhase, dash?.stop_reason)
    : etaToBudget(usedUsd, budgetUsd, cycleStartedAt);
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
  // SERVED, because the browser cannot answer either: what one sample costs is the connector's
  // declaration, not "is this self-optimization?". `1` disables the control WITH ITS REASON —
  // it used to accept presses the engine silently pinned to depth 1.
  const maxCells = dash?.max_cells_in_flight ?? 1;
  const perGroup = dash?.concurrency_arming === "batch";
  // Unlike Skip, this one follows the VIEWED path: the ceiling on screen and the cycle the press
  // arms are the same run at either depth. The outer's arming is deliberately not inherited
  // (`runner/entry.py`), so each layer is armed by looking at it.
  const concurrencyReason =
    maxCells <= 1
      ? "This backend runs one sample at a time — a single call has no latency to overlap."
      : undefined;
  const armDisabled = Boolean(concurrencyReason) || runPhase !== "running" || pending !== null;
  // SERVED: what the operator asked for and the walk has not spent yet. Between a press and the
  // next launch boundary this is the only thing that says the press landed — `lookahead` is what
  // the loop HELD and still reads 1.
  const armedCells = dash?.sample_lookahead_armed ?? 1;
  const armPending = armedCells > 1 && armedCells !== lookahead;
  // The operator's effective choice across both phases: their pending request while it waits,
  // then what the loop actually held once the group is released.
  const shownCells = Math.max(armedCells, lookahead);
  const concurrencyTitle =
    concurrencyReason ??
    `${
      armPending
        ? `${armedCells} armed — releasing together at the next sample boundary. `
        : ""
    }${
      perGroup
        ? "Releases this many samples together and waits for all of them; one press buys one group."
        : `Runs the next round's scoring this many samples in flight, then disarms.`
    } Ceiling ${maxCells}. The measurement is unchanged and the cycle is NOT marked babysat${
      discards > 0 ? ` (${discards} discarded)` : ""
    }.`;
  // Clamped here as well as in the walk: the server records the request UNCLAMPED, so a value
  // past the ceiling would read back as an arming the run never held.
  const armCells = (raw: number | string) => {
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 1) return;
    const cells = Math.min(n, maxCells);
    if (cells === shownCells) return;
    void act("sample-lookahead", () =>
      postSetSampleLookahead(viewedPath ?? [{ campaignId, cycleId }], cells),
    );
  };

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
      className={cx(
        "remote-control",
        open && "remote-control-open",
        offline && "remote-control-offline",
      )}
      role="group"
      aria-label="Campaign remote control"
    >
      {/* Opens UPWARD — the strip is bottom-fixed, so the panel stacks above the controls
          rather than below them. It folds in what was a second, chat-only job bar: one
          surface answers "what is this run doing and costing", on every tab. */}
      {open && (
        <div className="remote-panel" role="region" aria-label="Job status and configuration">
          <div className="remote-panel-section">
            <div className="section-title">Identity</div>
            <div className="row"><span className="lbl">Unit</span><span className="val">{fmtText(cycleId)}</span></div>
            <div className="row"><span className="lbl">Session</span><span className="val">{fmtText(dash?.session_id)}</span></div>
            <div className="row"><span className="lbl">Project</span><span className="val">{fmtText(leafEntry?.dataset_name)}</span></div>
            <div className="row"><span className="lbl">Updated</span><span className="val">{fmtText(dash?.wallclock_serialized_at)}</span></div>
            <div className="section-title">Spend</div>
            <div className="row"><span className="lbl">Backend</span><span className="val">{rateKnown ? fmtUsd(backendUsd) : `${backendTokens} tok`}</span></div>
            <div className="row"><span className="lbl">Loop</span><span className="val">{rateKnown ? fmtUsd(loopUsd) : `${loopTokens} tok`}</span></div>
            <div className="row"><span className="lbl">Total</span><span className="val">{usedUsd != null ? fmtUsd(usedUsd) : "—"}</span></div>
            <div className="row"><span className="lbl">Tokens</span><span className="val">{fmtTokens(totalTokens)}</span></div>
          </div>
          <div className="remote-panel-section" title={TERMS.newjob_bar_adjust}>
            <div className="section-title">Finishing criteria</div>
            <SpendBudgetControl
              currentBudgetUsd={budgetUsd}
              currentBudgetTokens={budgetTokens}
              usedUsd={usedUsd}
              usedTokens={totalTokens}
            />
          </div>
        </div>
      )}
      {/* Inner/outer drill — navigation, not a run command. One slot, two forms:
          viewing the outer of a live self-optimizing run offers the hop INTO the
          inner cycle currently running; viewing an inner offers the hop back out.
          The full unit identity lives in the Dashboard masthead, not here. */}
      {inner ? (
        <button
          type="button"
          className="remote-btn remote-drill"
          onClick={backToOuter}
          aria-label="Return to the outer campaign"
          title="Viewing an inner run's dashboard. Return to the outer campaign."
        >
          <span aria-hidden="true">↑</span>
          <span className="remote-btn-label">outer</span>
        </button>
      ) : innerHop ? (
        <button
          type="button"
          className="remote-btn remote-drill"
          onClick={() => drillInto(innerHop.campaignId, innerHop.cycleId)}
          aria-label="Open the running inner cycle's dashboard"
          title={`An inner run is live — ${innerHop.campaignId}. Open its dashboard.`}
        >
          <span aria-hidden="true">⤷</span>
          <span className="remote-btn-label">inner</span>
        </button>
      ) : null}
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
      <RunControlButton disabledReason={innerReason} />
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
      {/* An ARM control, not a switch: it spends itself. `batch` is a FIELD rather than a
          button, because only there does the operator pick the number — and an input nested
          inside a button is neither valid nor operable. */}
      {perGroup ? (
        // An EXCLUSIVE choice over a range the connector caps at 4, so: the shared segmented
        // control. One click is one press, which removes the commit question entirely.
        <span className="remote-cells-group" title={concurrencyTitle}>
          <span aria-hidden="true" className="remote-cells-icon">
            ⇉
          </span>
          <SegmentedControl
            ariaLabel={`Samples to release together, up to ${maxCells}`}
            className={cx("remote-cells", armPending && "pending")}
            value={String(shownCells)}
            onChange={(v) => armCells(v)}
            options={Array.from({ length: maxCells }, (_, i) => ({
              value: String(i + 1),
              label: String(i + 1),
              disabled: armDisabled,
              title: i === 0 ? "One at a time" : `Release ${i + 1} together`,
            }))}
          />
        </span>
      ) : (
        <button
          type="button"
          className={cx("remote-btn", "remote-sample-lookahead", sampleLookaheadArmed && "armed")}
          onClick={() => armCells(sampleLookaheadArmed ? 1 : maxCells)}
          disabled={armDisabled}
          aria-pressed={sampleLookaheadArmed}
          aria-label={`Samples in flight, up to ${maxCells}`}
          title={concurrencyTitle}
        >
          <span aria-hidden="true">⇉</span>
          <span className="remote-btn-label">
            {sampleLookaheadArmed ? `${lookahead}×` : "Look-ahead"}
          </span>
        </button>
      )}
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
        {unpricedTokens > 0 ? (
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
      {/* The outcome readout, and the panel's own toggle. It reads as decoration mid-run and
          as the answer once the run stops — which is why the strip now survives `terminal`
          and `detached` rather than unmounting exactly when these numbers start mattering. */}
      <button
        type="button"
        className="remote-readout"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        aria-label="Job status and configuration"
      >
        <span className="chip" title={TERMS.newjob_bar_best}>
          <span className="chip-lbl">Lift</span> <strong>{deltaTheta}</strong>
          {best != null && <span className="chip-origin"> · best {fmtPct0(best)}</span>}
        </span>
        <span className="chip" title={terminal ? TERMS.newjob_bar_best : TERMS.newjob_bar_eta}>
          <span className="chip-lbl">{terminal ? "Ended" : "ETA"}</span> <strong>{etaChip}</strong>
        </span>
        <span className="chip" title={TERMS.newjob_bar_eff}>
          <span className="chip-lbl">Δ/$</span> <strong>{effChip}</strong>
        </span>
        <svg className="chev" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m3 4.5 3 3 3-3" />
        </svg>
      </button>
      {err ? (
        <span className="remote-err" role="alert">
          {err}
        </span>
      ) : null}
    </div>
  );
}

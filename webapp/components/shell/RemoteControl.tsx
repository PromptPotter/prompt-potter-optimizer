"use client";
import { useMemo, useState, type ReactNode } from "react";
import { postSkipSearchpoint, postSetSampleLookahead, type BackpressureReading } from "@/lib/api";
import { SegmentedControl, Switch, Term } from "@/components/ui";
import { useCommand } from "@/lib/hooks/useCommand";
import { cx } from "@/lib/cx";
import { TERMS } from "@/lib/terms";
import { headlineStats, pathOf, prefixReading, readSpend, runningInnerRun } from "@/lib/derivations";
import { fmtText, fmtDuration, fmtTheta, fmtUsd, fmtTokens } from "@/lib/format";
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
// It consolidates the run controls that were scattered (play/pause was buried in
// the Chat-tab heat-map). The STRIP carries only what it ACTS on — play/pause (the
// reused RunControlButton), Skip, the drill, and the Lift readout that toggles the
// panel; every other number and control is a row in that panel, because nine slots on
// one bar is a paragraph, not a remote. WHERE the run is — phase, round, best, spend —
// is the masthead's chip row, one band per fact and this one not repeating it.
// Pause is the single interrupt verb — there is no separate Stop;
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

// The call a round's next decision waits on, once it has outlasted an ordinary call — module-level
// for the same wallclock reason as `etaToBudget`. Calls are taken in walk order, so one slow call
// at a candidate's head holds every call behind it, and a stall should say which.
function heldBy(waitingOn: string | null, waitingSince: number | null): string | null {
  if (waitingOn === null || waitingSince === null) return null;
  const waited = Date.now() / 1000 - waitingSince;
  return waited >= 10 ? `waiting on ${waitingOn} · ${fmtDuration(waited)}` : null;
}

// The model provider holding calls that are out but not yet sent — module-level for the same
// wallclock reason. Served only while it holds something, so its presence alone is the signal.
function providerHold(held: BackpressureReading): string {
  const now = Date.now() / 1000;
  const pace = held.at_once === null ? "" : ` · ${held.at_once} at once`;
  if (held.since === null) return `answering again${pace}`;
  const cooling = held.resumes_at === null ? 0 : held.resumes_at - now;
  const next = cooling > 0 ? `next send in ${fmtDuration(cooling)}` : "probing";
  return `rate-limited ${fmtDuration(now - held.since)} · ${next}${pace}`;
}

// One bucket's prefix-cache discount, appended to that bucket's own spend figure. A bucket holds
// billed calls only (`readSpend`), so `replayed` is false by construction — and the row states
// which of the three it is, since a cold prefix and an unreporting provider cost differently.
function cacheTag(share: number | null, write: number): ReactNode {
  const prefix = prefixReading(share, false);
  // The WRITE beside the read is the economics: a write makes the next read cheap, so writes with
  // no reads is paying a premium to fill a prefix nothing collects.
  const wrote = write > 0 ? ` ·w${fmtTokens(write)}` : "";
  return (
    <span className="remote-spend-cache" title={prefix.title}>
      {` · ${prefix.label}${wrote}`}
    </span>
  );
}

interface Props {
  // The viewed cycle's creation stamp, for the burn-rate ETA. Shell-owned because the
  // leaf hop's own stamp is what the strip describes.
  cycleStartedAt?: string | null;
}

export function RemoteControl({ cycleStartedAt = null }: Props) {
  // Identity from the workspace; live state from the per-cycle dashboard stream.
  const {
    campaignId,
    cycleId,
    leafCampaignId,
    leafCycleId,
    leafIsL4,
    viewedPath,
    cycles,
    drillInto,
    backToOuter,
  } = useWorkspace();
  const { dash, status } = useDashboard();
  const cmd = useCommand<"skip" | "sample-lookahead">("remote-control");
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
  // `hasLiveProducer` left both of those arms declared and unreachable, so a budget-halted or
  // crashed cycle simply lost its play button. Those are also the two states where the
  // readout below matters most: what the run got, and what it cost.
  if (runPhase === null || runPhase === "checkin") return null;
  const terminal = runPhase === "terminal";
  const offline = status === "offline";

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
  // One parser for the whole block, ceilings included — `readSpend` already reads the armed
  // `run_limits` pair (the gate's own source). This bar used to re-spell the USD ceiling
  // inline, which is the second spelling that lets two surfaces disagree.
  const {
    backendUsd,
    loopUsd,
    judgeUsd,
    usedUsd,
    budgetUsd,
    budgetTokens,
    rateKnown,
    backendTokens,
    loopTokens,
    judgeTokens,
    totalTokens,
    unpricedTokens,
    backendCacheShare,
    loopCacheShare,
    judgeCacheShare,
    backendCacheWrite,
    loopCacheWrite,
    judgeCacheWrite,
  } = readSpend(dash);

  // Headline KPIs. `abilityDelta` is SERVED and is in LOGITS, so it renders as θ and never as
  // a percent — the two are different bases, not different renderings. The readout carries the
  // LIFT alone; the absolute best is the masthead's BEST chip, and saying it twice let the two
  // disagree on screen.
  const { abilityDelta, abilityDeltaPerUsd } = headlineStats(dash);
  const deltaTheta = fmtTheta(abilityDelta);
  const effChip = abilityDeltaPerUsd != null ? `${abilityDeltaPerUsd.toFixed(2)} θ/$` : "—";
  const etaChip = etaToBudget(usedUsd, budgetUsd, cycleStartedAt);
  // SERVER state, never a local toggle (I6): the depth clears itself at the round boundary, so a
  // client-held value would stay lit after the round that spent it. ONE served number — the depth
  // in force — because the walk re-reads it at every launch: a press applies to a walk already
  // running, and waits harmlessly for the next one if none is.
  const lookahead = dash?.sample_lookahead ?? 1;
  const autoArmed = dash?.sample_lookahead_auto ?? false;
  const discards = dash?.sample_lookahead_discards ?? 0;
  // SERVED, because the browser cannot answer either: what one sample costs is the connector's
  // declaration, not "is this self-optimization?". `1` disables the control WITH ITS REASON —
  // unserved, it takes presses the engine then silently pins to depth 1.
  const maxCells = dash?.max_cells_in_flight ?? 1;
  // SERVED and summed over every candidate the round is walking plus the PoBB catch-ups — never a
  // count of `open_sample_ids`, which sees only the candidate whose turn it is. Between rounds
  // `most` is the next round's, so a press can be sized before it applies.
  const inFlight = dash?.in_flight ?? 0;
  const allowed = dash?.lookahead_allowed ?? 0;
  const most = dash?.lookahead_most ?? 0;
  const scoringNow = inFlight > 0 || allowed > 0;
  // SERVED, and the fourth bound on this one depth: a cell RESERVES its worst case against the
  // spend ceiling, so a ceiling only a few of those wide holds the walk at one call while the
  // depth reads armed and the stop rules read generous. Nothing on this panel could say so.
  const affordable = dash?.lookahead_affordable ?? null;
  const cellReserve = dash?.cell_reserve_usd ?? null;
  const moneyPinned = affordable !== null && inFlight + affordable < Math.min(lookahead, allowed);
  const waitNote = heldBy(dash?.waiting_on ?? null, dash?.waiting_since ?? null);
  // Guarded, not annotated: `dashboard.json` is served verbatim, and a file an earlier build
  // wrote carries no such key.
  const providerHeld = dash?.backpressure ?? null;
  // The deepest press worth making: what the backend takes, and no more than the round can hold.
  const pickMax = most > 0 ? Math.max(1, Math.min(maxCells, most)) : maxCells;
  // Unlike Skip, this one follows the VIEWED path: the ceiling on screen and the cycle the press
  // arms are the same run at either depth. The outer's arming is deliberately not inherited
  // (`runner/entry.py`), so each layer is armed by looking at it.
  const concurrencyReason =
    maxCells <= 1
      ? "This backend runs one sample at a time — a single call has no latency to overlap."
      : undefined;
  const armDisabled =
    Boolean(concurrencyReason) || runPhase !== "running" || cmd.pending !== null;
  // A mode, not a press, so it is offered on a paused run too: it survives the relaunch.
  const autoDisabled = Boolean(concurrencyReason) || terminal || cmd.pending !== null;
  const concurrencyTitle =
    concurrencyReason ??
    `${
      autoArmed
        ? `Every round, scoring holds as many calls as the stop rules allow, up to ${maxCells}`
        : `Runs this round's scoring with this many calls in flight, up to ${pickMax}, then resets to 1`
    }. A cut discards at most one call. The measurement is unchanged and the cycle is NOT marked babysat${
      discards > 0 ? ` (${discards} discarded)` : ""
    }.`;
  const arm = (cells: number, auto: boolean) =>
    void cmd.run("sample-lookahead", () =>
      postSetSampleLookahead(viewedPath ?? [{ campaignId, cycleId }], cells, auto),
    );
  // Clamped here as well as in the walk: the server records the request UNCLAMPED, so a value
  // past the ceiling would read back as an arming the run never held.
  // A press is for this round, so picking a depth while "Every round" stands switches it off.
  const armCells = (raw: string) => {
    const cells = Number(raw);
    if (cells === lookahead && !autoArmed) return;
    arm(cells, false);
  };
  // One segment per depth the backend takes, doubling as the round's gauge: lit as far as calls
  // are out, tinted as far as the stop rules allow, and off past the most the round could hold.
  const depthSegments = Array.from({ length: maxCells }, (_, i) => {
    const n = i + 1;
    const fill: "full" | "part" | undefined =
      n <= inFlight ? "full" : n <= allowed ? "part" : undefined;
    return {
      value: String(n),
      label: String(n),
      fill,
      disabled: armDisabled || n > pickMax,
      title: n === 1 ? "One at a time" : `Hold ${n} in flight`,
    };
  });

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
            {/* The prefix-cache discount rides its OWN bucket's row rather than a headline of its
                own: the three buckets prompt different models through different providers, so
                there is no one share to state — and pooling them hands the backend's ratio to
                everybody, drowning the judge, whose rubric is a module constant and whose prefix
                pays best. Silent at a real 0, like every other share on this panel. */}
            <div className="row"><span className="lbl">Backend</span><span className="val">{rateKnown ? fmtUsd(backendUsd) : `${backendTokens} tok`}{cacheTag(backendCacheShare, backendCacheWrite)}</span></div>
            <div className="row"><span className="lbl">Loop</span><span className="val">{rateKnown ? fmtUsd(loopUsd) : `${loopTokens} tok`}{cacheTag(loopCacheShare, loopCacheWrite)}</span></div>
            <div className="row"><span className="lbl">Judge</span><span className="val">{rateKnown ? fmtUsd(judgeUsd) : `${judgeTokens} tok`}{cacheTag(judgeCacheShare, judgeCacheWrite)}</span></div>
            <div className="row"><span className="lbl">Total</span><span className="val">{usedUsd != null ? fmtUsd(usedUsd) : "—"}</span></div>
            <div className="row"><span className="lbl">Tokens</span><span className="val">{fmtTokens(totalTokens)}</span></div>
            {unpricedTokens > 0 ? (
              <div className="row">
                <span className="lbl">USD cap</span>
                <Term className="val remote-spend-warn" content="USD cost couldn't be resolved for some calls (e.g. Groq returns no wire cost and the model isn't in the rate table). The $ figure undercounts real spend and the USD cap can't see it — the token cap is the backstop.">
                  <span aria-hidden="true">⚠</span> inactive
                </Term>
              </div>
            ) : null}
            <div className="section-title">Outcome</div>
            {!terminal && (
              <Term className="row" content={TERMS.remote_eta}>
                <span className="lbl">ETA</span><span className="val">{etaChip}</span>
              </Term>
            )}
            <Term className="row" content={TERMS.remote_eff}>
              <span className="lbl">Δ/$</span><span className="val">{effChip}</span>
            </Term>
            {babysat ? (
              <div className="row">
                <span className="lbl">Provenance</span>
                <Term className="val remote-babysat" content="An operator manually intervened (skip) — this cycle is no longer purely reproducible.">
                  <span aria-hidden="true">✎</span> babysat
                </Term>
              </div>
            ) : null}
          </div>
          {/* Two forms of one arming, and the depth is never the browser's guess. Every round: no
              number at all — the round holds what its stop rules allow, up to the backend's
              ceiling, so the top segment is on and the fill is the round's live amplitude.
              Otherwise a PRESS for this round's scoring, spent at its boundary. The fill and the
              readout are both the served gauge. */}
          <div className="remote-panel-section">
            <div className="section-title">Samples in flight</div>
            <Term className="row" content={TERMS.remote_flight}>
              <span className="lbl">{scoringNow ? "Now" : "Next round"}</span>
              <span className="val">
                {scoringNow
                  ? `${inFlight} out · ${allowed} allowed · ${most} at most`
                  : `${most} at most`}
              </span>
            </Term>
            {waitNote ? (
              <div className="row">
                <span className="lbl">Held by</span>
                <span className="val remote-spend-warn">{waitNote}</span>
              </div>
            ) : null}
            {moneyPinned ? (
              <div className="row">
                <span className="lbl">Ceiling</span>
                <Term
                  className="val remote-spend-warn"
                  content={`A cell is admitted on the MOST it could bill${
                    cellReserve != null ? `, ${fmtUsd(cellReserve)} here` : ""
                  }, so once the ceiling cannot hold another of those the walk runs one at a time — whatever depth is armed. Raise the budget below to widen it.`}
                >
                  {affordable === 0 ? "holds no further cell" : `${affordable} more affordable`}
                </Term>
              </div>
            ) : null}
            {providerHeld ? (
              <div className="row">
                <span className="lbl">Provider</span>
                <Term className="val remote-spend-warn" content={providerHeld.detail}>
                  {providerHold(providerHeld)}
                </Term>
              </div>
            ) : null}
            <span className="remote-cells-group" title={concurrencyTitle}>
              <span aria-hidden="true" className="remote-cells-icon">
                ⇉
              </span>
              <SegmentedControl
                ariaLabel={`Calls to hold in flight, up to ${pickMax}`}
                className="remote-cells"
                value={String(lookahead)}
                onChange={armCells}
                options={depthSegments}
              />
            </span>
            <div className="row">
              <span className="lbl">Every round</span>
              <span className="val">
                <Switch
                  checked={autoArmed}
                  label="Hold as many as the stop rules allow, every round"
                  locked={autoDisabled}
                  lockedNote={concurrencyReason ?? "not available now"}
                  onChange={() => arm(lookahead, !autoArmed)}
                />
              </span>
            </div>
          </div>
          <div className="remote-panel-section">
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
      {offline ? (
        <Term
          className="remote-offline"
          content="Connection to the server was lost — showing the last known state."
        >
          <span aria-hidden="true">⭘</span> reconnecting
        </Term>
      ) : null}
      <RunControlButton disabledReason={innerReason} />
      <button
        type="button"
        className="remote-btn remote-skip"
        onClick={() => void cmd.run("skip", () => postSkipSearchpoint(campaignId, cycleId))}
        disabled={inner || runPhase !== "running" || cmd.pending !== null}
        aria-label="Skip the rest of this searchpoint"
        title={
          innerReason ??
          "Cut the remaining samples of the searchpoint scoring now, accept the partial, and keep the cycle running. Marks the cycle babysat."
        }
      >
        {SKIP_ICON}
        <span className="remote-btn-label">Skip</span>
      </button>
      {/* The one headline number, and the panel's own toggle. Everything the strip used to
          spell out — spend, ETA, Δ/$, look-ahead, the babysat and unpriced tags — is a row
          in that panel now: a control strip is read at a glance, and nine slots is a
          paragraph. It reads as decoration mid-run and as the answer once the run stops,
          which is why the strip survives `terminal` rather than unmounting exactly when
          these numbers start mattering. */}
      {/* The chip TEACHES and the chevron ACTS — two things, so two elements. Folded into one
          control, the chip's HoverCard trigger is a focusable descendant of it, and reading the
          term presses the control. */}
      <div className={cx("remote-readout", open && "remote-readout-on")}>
        <Term className="chip" content={TERMS.remote_best}>
          <span className="chip-lbl">Lift</span> <strong>{deltaTheta}</strong>
        </Term>
        <button
          type="button"
          className="remote-readout-toggle"
          aria-expanded={open}
          aria-label="Job status and configuration"
          onClick={() => setOpen((v) => !v)}
        >
          <svg className="chev" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m3 4.5 3 3 3-3" />
          </svg>
        </button>
      </div>
      {cmd.failure ? (
        <span className="remote-err" role="alert">
          {cmd.failure.message}
        </span>
      ) : null}
    </div>
  );
}

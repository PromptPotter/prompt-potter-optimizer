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
import { RunLimitsControl } from "@/components/dashboard/control/RunLimitsControl";

// The global remote, bottom-fixed on every tab: the strip carries only what it ACTS on, every
// other number is a panel row, and WHERE the run is stays RunMasthead's.

const SKIP_ICON = (
  <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
    <path d="M4 3.2v9.6l6-4.8z" />
    <rect x="10.5" y="3.2" width="2.2" height="9.6" rx="1" />
  </svg>
);

// Module-level, not a hook, so the wallclock read is allowed (as for the two helpers below).
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
  const burn = usedUsd / ageSec;
  const remainingSec = (budgetUsd - usedUsd) / burn;
  return fmtDuration(remainingSec);
}

// Calls are taken in walk order, so one slow call at a candidate's head holds every call behind it.
function heldBy(waitingOn: string | null, waitingSince: number | null): string | null {
  if (waitingOn === null || waitingSince === null) return null;
  const waited = Date.now() / 1000 - waitingSince;
  return waited >= 10 ? `waiting on ${waitingOn} · ${fmtDuration(waited)}` : null;
}

// `backpressure` is served only while the provider holds something; its presence is the signal.
function providerHold(held: BackpressureReading): string {
  const now = Date.now() / 1000;
  const pace = held.at_once === null ? "" : ` · ${held.at_once} at once`;
  if (held.since === null) return `answering again${pace}`;
  const cooling = held.resumes_at === null ? 0 : held.resumes_at - now;
  const next = cooling > 0 ? `next send in ${fmtDuration(cooling)}` : "probing";
  return `rate-limited ${fmtDuration(now - held.since)} · ${next}${pace}`;
}

// A bucket holds billed calls only (`readSpend`), so `replayed` is false by construction.
function cacheTag(share: number | null, write: number): ReactNode {
  const prefix = prefixReading(share, false);
  // Writes with no reads is paying a premium to fill a prefix nothing collects.
  const wrote = write > 0 ? ` ·w${fmtTokens(write)}` : "";
  return (
    <span className="remote-spend-cache" title={prefix.title}>
      {` · ${prefix.label}${wrote}`}
    </span>
  );
}

interface Props {
  cycleStartedAt?: string | null;
}

export function RemoteControl({ cycleStartedAt = null }: Props) {
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
  const isOuterView = (viewedPath?.length ?? 1) === 1;
  const { root: lineageRoot } = useLineageTree(
    viewedPath ?? [],
    Boolean(viewedPath) && isOuterView && leafIsL4,
  );
  const innerRun = useMemo(() => runningInnerRun(lineageRoot), [lineageRoot]);

  if (!campaignId || !cycleId) return null;

  const runPhase = dash?.run_phase ?? null;
  // SURVIVES `terminal` and `detached`: `PHASE_ACTION` maps both to "start", and this is
  // `RunControlButton`'s only mount.
  if (runPhase === null || runPhase === "checkin") return null;
  const terminal = runPhase === "terminal";
  const offline = status === "offline";

  // The phase is the LEAF's but Skip/Pause address the ROOT hop, so an inner view disables
  // them with the reason rather than re-targeting (I3). Concurrency descends — see below.
  const inner = !isOuterView;
  const innerReason = inner
    ? "Run control reaches the outer campaign only — back out of this inner run to use it."
    : undefined;
  // `pathOf` because an inner cycle_id repeats across sandboxes — the path is the address.
  const innerHop = innerRun ? pathOf(innerRun).at(-1) : undefined;

  // An inner cycle is absent from `/cycles`, so it claims no babysat mark of the outer's.
  const leafEntry = cycles.find(
    (c) => c.campaign_id === leafCampaignId && c.cycle_id === leafCycleId,
  );
  const babysat = Boolean(leafEntry?.human_intervened);
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

  // `abilityDelta` is in LOGITS, so it renders as θ and never as a percent.
  const { abilityDelta, abilityDeltaPerUsd } = headlineStats(dash);
  const deltaTheta = fmtTheta(abilityDelta);
  const effChip = abilityDeltaPerUsd != null ? `${abilityDeltaPerUsd.toFixed(2)} θ/$` : "—";
  const etaChip = etaToBudget(usedUsd, budgetUsd, cycleStartedAt);
  // SERVER state (I6): the depth clears itself at the round boundary, and the walk re-reads it
  // at every launch, so a press applies to a walk already running.
  const lookahead = dash?.sample_lookahead ?? 1;
  const autoArmed = dash?.sample_lookahead_auto ?? false;
  const discards = dash?.sample_lookahead_discards ?? 0;
  // `1` disables the control WITH ITS REASON; unserved, presses would be silently pinned to 1.
  const maxCells = dash?.max_cells_in_flight ?? 1;
  // Summed server-side over every candidate walking plus PoBB catch-ups — never a count of
  // `open_sample_ids`. Between rounds `most` is the next round's.
  const inFlight = dash?.in_flight ?? 0;
  const allowed = dash?.lookahead_allowed ?? 0;
  const most = dash?.lookahead_most ?? 0;
  const scoringNow = inFlight > 0 || allowed > 0;
  // A cell RESERVES its worst case against the spend ceiling, so a tight ceiling holds the walk
  // at one call while the depth reads armed.
  const affordable = dash?.lookahead_affordable ?? null;
  const cellReserve = dash?.cell_reserve_usd ?? null;
  const moneyPinned = affordable !== null && inFlight + affordable < Math.min(lookahead, allowed);
  const waitNote = heldBy(dash?.waiting_on ?? null, dash?.waiting_since ?? null);
  // Guarded, not annotated: `dashboard.json` is served verbatim and may lack the key.
  const providerHeld = dash?.backpressure ?? null;
  const pickMax = most > 0 ? Math.max(1, Math.min(maxCells, most)) : maxCells;
  // Unlike Skip, this follows the VIEWED path; the outer's arming is not inherited
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
  // Clamped here too: the server records the request UNCLAMPED, so a value past the ceiling
  // would read back as an arming the run never held.
  const armCells = (raw: string) => {
    const cells = Number(raw);
    if (cells === lookahead && !autoArmed) return;
    arm(cells, false);
  };
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
      {open && (
        <div className="remote-panel" role="region" aria-label="Job status and configuration">
          <div className="remote-panel-section">
            <div className="section-title">Identity</div>
            <div className="row"><span className="lbl">Unit</span><span className="val">{fmtText(cycleId)}</span></div>
            <div className="row"><span className="lbl">Session</span><span className="val">{fmtText(dash?.session_id)}</span></div>
            <div className="row"><span className="lbl">Project</span><span className="val">{fmtText(leafEntry?.dataset_name)}</span></div>
            <div className="row"><span className="lbl">Updated</span><span className="val">{fmtText(dash?.wallclock_serialized_at)}</span></div>
            <div className="section-title">Spend</div>
            {/* Cache share per bucket, never pooled: the buckets hit different providers, and a
                pooled ratio would drown the judge's. */}
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
            <RunLimitsControl
              currentBudgetUsd={budgetUsd}
              currentBudgetTokens={budgetTokens}
              currentMaxRounds={dash?.run_limits?.max_rounds ?? null}
              usedUsd={usedUsd}
              usedTokens={totalTokens}
            />
          </div>
        </div>
      )}
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
      {/* The chip TEACHES and the chevron ACTS: folded into one control, the chip's HoverCard
          trigger would be a focusable descendant, and reading the term would press it. */}
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

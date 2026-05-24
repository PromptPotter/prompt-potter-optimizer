"use client";
import { TERMS } from "@/lib/terms";
import { roundOf, type DashboardSnapshot, type StatusKind } from "@/lib/poll";
import { cycleStatusLabel } from "@/lib/cycle-status";
import { fmtPct1, fmtSecs, fmtUsd, fmtTokens, ageText } from "@/lib/format";
import { StopButton } from "@/components/dashboard/StopButton";

// Pinned status row. Visible on every tab (Chat / Dashboard / Files) so the
// operator always sees the running state + spend + Stop affordance without
// switching tabs. Replaces the dashboard-tab-only DashStatusStrip and the
// EditMode-gated StopButton that used to hide inside the dashboard
// breadcrumb. Spend lifted from ChatPane's chip row so it's visible at the
// chrome level, not buried in a tab.

interface Props {
  status: StatusKind;
  statusText: string;
  statusHint?: string;
  termKey?: string;
  campaignId: string | null;
  cycleId: string | null;
  dash: DashboardSnapshot | null;
  isLive: boolean;
  onOpenFiles: () => void;
}

function shortCycleId(id: string | null): string {
  if (!id) return "—";
  return id.length > 22 ? `${id.slice(0, 14)}…${id.slice(-4)}` : id;
}

interface SpendBucket {
  used_usd?: number;
  input_tokens?: number;
  output_tokens?: number;
  rate_known?: boolean;
}

interface SpendBlock {
  backend?: SpendBucket;
  loop?: SpendBucket;
  total_used_usd?: number;
  budget_usd?: number | null;
}

// Cycle spend → headline chip. Mirrors the truth-table the ChatPane job-bar
// uses: rate-known providers (OpenRouter on the wire, mapped through
// shared/spend.py for the rest) yield USD; otherwise we fall back to a
// token-count display so the operator still has a concrete number.
function readSpend(dash: DashboardSnapshot | null): { chip: string; tooltip: string } {
  const block = (dash as Record<string, unknown> | null)?.spend as SpendBlock | undefined;
  const fallbackTip = TERMS.newjob_bar_spend ?? "Campaign spend";
  if (!block) return { chip: "—", tooltip: fallbackTip };
  const backend = block.backend ?? {};
  const loop = block.loop ?? {};
  const backendUsd = typeof backend.used_usd === "number" ? backend.used_usd : 0;
  const loopUsd = typeof loop.used_usd === "number" ? loop.used_usd : 0;
  const totalUsd =
    typeof block.total_used_usd === "number" ? block.total_used_usd : backendUsd + loopUsd;
  const rateKnown = !!(backend.rate_known || loop.rate_known);
  const totalTokens =
    (backend.input_tokens ?? 0) +
    (backend.output_tokens ?? 0) +
    (loop.input_tokens ?? 0) +
    (loop.output_tokens ?? 0);
  const chip = rateKnown
    ? fmtUsd(totalUsd)
    : totalTokens > 0
      ? fmtTokens(totalTokens)
      : "—";
  const tooltip =
    rateKnown && (backendUsd > 0 || loopUsd > 0)
      ? `Backend ${fmtUsd(backendUsd)} • Loop ${fmtUsd(loopUsd)}`
      : fallbackTip;
  return { chip, tooltip };
}

export function StatusBar({
  status,
  statusText,
  statusHint,
  termKey,
  campaignId,
  cycleId,
  dash,
  isLive,
  onOpenFiles,
}: Props) {
  const tip = termKey ? TERMS[termKey] : "";
  const round = roundOf(dash);
  // One canonical status word — collapses dashboard `state`/`stop_reason`
  // to the same vocabulary the cycle list (index.json `status`) uses, so a
  // stopped+interrupted cycle reads "interrupted" here too, not "stopped".
  const phase = dash?.state
    ? cycleStatusLabel(
        dash.state as string,
        (dash as { stop_reason?: string } | null)?.stop_reason,
      )
    : null;
  const best = typeof dash?.best === "number" ? dash.best : null;
  const origin = typeof dash?.origin?.accuracy === "number" ? dash.origin.accuracy : null;
  const delta = best != null && origin != null ? best - origin : null;
  const deltaSign = delta == null ? "" : delta > 0 ? "+" : "";
  const deltaCls = delta == null ? "" : delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  const lastQueryS = dash?.last_query_elapsed_s;
  const lastQuery =
    typeof lastQueryS === "number" && Number.isFinite(lastQueryS)
      ? fmtSecs(lastQueryS)
      : null;
  const spend = readSpend(dash);
  return (
    <div
      className={`dash-strip status-bar ${status}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      title={tip || undefined}
    >
      <span className="status-dot" aria-hidden="true" />
      <span className="dash-strip-text">
        <strong>{statusText}</strong>
        {statusHint ? <span className="dash-strip-hint">{statusHint}</span> : null}
      </span>
      <span className="dash-strip-sep" aria-hidden="true">
        ·
      </span>
      <span className="dash-strip-cell" title={cycleId ?? ""}>
        <span className="dash-strip-label">unit</span>
        <code>{shortCycleId(cycleId)}</code>
      </span>
      <span className="dash-strip-cell">
        <span className="dash-strip-label">round</span>
        <strong>{round != null ? `R${round}` : "—"}</strong>
        {phase ? <span className="dash-strip-sub">· {phase}</span> : null}
      </span>
      <span className="dash-strip-cell">
        <span className="dash-strip-label">best</span>
        <strong>{fmtPct1(best)}</strong>
        {delta != null && origin != null ? (
          <span className={`dash-strip-delta ${deltaCls}`}>
            {deltaSign}
            {(delta * 100).toFixed(1)}% vs origin
          </span>
        ) : null}
      </span>
      <span className="dash-strip-cell" title={spend.tooltip}>
        <span className="dash-strip-label">spend</span>
        <strong>{spend.chip}</strong>
      </span>
      {lastQuery ? (
        <span className="dash-strip-cell">
          <span className="dash-strip-label">last</span>
          <strong>{lastQuery}</strong>
        </span>
      ) : null}
      <span className="dash-strip-cell">
        <span className="dash-strip-label">updated</span>
        <strong>{ageText(dash?.wallclock_serialized_at)}</strong>
      </span>
      {campaignId && cycleId && isLive ? (
        <StopButton campaignId={campaignId} cycleId={cycleId} isLive={isLive} />
      ) : null}
      <button
        type="button"
        className="dash-strip-jump"
        onClick={onOpenFiles}
        aria-label="Open files pane"
      >
        Files →
      </button>
    </div>
  );
}

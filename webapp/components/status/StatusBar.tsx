"use client";
import { TERMS } from "@/lib/terms";
import { roundOf, type DashboardSnapshot, type StatusKind } from "@/lib/poll";
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
  cycleId: string | null;
  dash: DashboardSnapshot | null;
  isLive: boolean;
  onOpenFiles: () => void;
}

function fmtPct(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";
}

function shortCycleId(id: string | null): string {
  if (!id) return "—";
  return id.length > 22 ? `${id.slice(0, 14)}…${id.slice(-4)}` : id;
}

function ageText(iso: string | undefined | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function fmtUsd(n: number): string {
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M tok`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k tok`;
  return `${n} tok`;
}

function fmtElapsed(s: number | null | undefined): string | null {
  if (typeof s !== "number" || !Number.isFinite(s)) return null;
  if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
  if (s < 60) return `${s.toFixed(2)}s`;
  return `${(s / 60).toFixed(1)}m`;
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
  cycleId,
  dash,
  isLive,
  onOpenFiles,
}: Props) {
  const tip = termKey ? TERMS[termKey] : "";
  const round = roundOf(dash);
  const patience = (dash as { patience?: string } | null)?.patience;
  const phase = (dash?.state as string | undefined) ?? null;
  const best = typeof dash?.best === "number" ? dash.best : null;
  const origin = typeof dash?.origin_accuracy === "number" ? dash.origin_accuracy : null;
  const delta = best != null && origin != null ? best - origin : null;
  const deltaSign = delta == null ? "" : delta > 0 ? "+" : "";
  const deltaCls = delta == null ? "" : delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  const lastQuery = fmtElapsed(dash?.last_query_elapsed_s);
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
        <span className="dash-strip-label">campaign</span>
        <code>{shortCycleId(cycleId)}</code>
      </span>
      <span className="dash-strip-cell">
        <span className="dash-strip-label">round</span>
        <strong>{round != null ? `R${round}` : "—"}</strong>
        {patience ? <span className="dash-strip-sub">· patience {patience}</span> : null}
        {phase ? <span className="dash-strip-sub">· {phase}</span> : null}
      </span>
      <span className="dash-strip-cell">
        <span className="dash-strip-label">best</span>
        <strong>{fmtPct(best)}</strong>
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
      {cycleId ? <StopButton cycleId={cycleId} isLive={isLive} /> : null}
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

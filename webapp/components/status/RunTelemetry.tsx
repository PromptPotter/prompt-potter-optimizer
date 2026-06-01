"use client";
import { TERMS } from "@/lib/terms";
import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { cycleStatusLabel } from "@/lib/cycle-status";
import { headlineStats } from "@/lib/derivations/headline-stats";
import { readSpend } from "@/lib/derivations/spend";
import { fmtPct1, fmtSecs, fmtUsd, fmtTokens, ageText } from "@/lib/format";
import { StopButton } from "@/components/dashboard/StopButton";

// Run-state telemetry strip — unit / round / best / spend / last / updated
// plus the Stop affordance. Renders inside the Console head row (see
// ConsolePane) so persistent run state sits at the chrome edge alongside
// the live tail, IDE-status-bar style. Banner framing ("frozen unit",
// "offline") lives in StatusAssistant at the top of <main>.

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  dash: DashboardSnapshot | null;
  isLive: boolean;
}

function shortCycleId(id: string | null): string {
  if (!id) return "—";
  return id.length > 22 ? `${id.slice(0, 14)}…${id.slice(-4)}` : id;
}

// Cycle spend → headline chip + tooltip. Rate-known providers (OpenRouter on
// the wire, mapped through shared/spend.py for the rest) yield USD; otherwise
// fall back to a token-count display so the operator still has a number. The
// numeric extraction is shared (`readSpend`); only the formatting lives here.
function spendChip(dash: DashboardSnapshot | null): { chip: string; tooltip: string } {
  const fallbackTip = TERMS.newjob_bar_spend ?? "Campaign spend";
  const s = readSpend(dash);
  const chip = s.rateKnown
    ? fmtUsd(s.totalUsd)
    : s.totalTokens > 0
      ? fmtTokens(s.totalTokens)
      : "—";
  const tooltip =
    s.rateKnown && (s.backendUsd > 0 || s.loopUsd > 0)
      ? `Backend ${fmtUsd(s.backendUsd)} • Loop ${fmtUsd(s.loopUsd)}`
      : fallbackTip;
  return { chip, tooltip };
}

export function RunTelemetry({ campaignId, cycleId, dash, isLive }: Props) {
  const round = roundOf(dash);
  // Live phase only — when the FSM is `stopped`, suppress the sub-label so
  // the round cell doesn't double-print the stop_reason that StatusAssistant
  // ("No live optimizer — viewing a frozen unit") already conveys.
  const stateValue = (dash?.state as string | undefined) ?? null;
  const phase =
    stateValue && stateValue !== "stopped"
      ? cycleStatusLabel(
          stateValue,
          (dash as { stop_reason?: string } | null)?.stop_reason,
        )
      : null;
  const { best, origin, delta } = headlineStats(dash);
  const deltaSign = delta == null ? "" : delta > 0 ? "+" : "";
  const deltaCls = delta == null ? "" : delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  const lastQueryS = dash?.last_query_elapsed_s;
  const lastQuery =
    typeof lastQueryS === "number" && Number.isFinite(lastQueryS)
      ? fmtSecs(lastQueryS)
      : null;
  const spend = spendChip(dash);
  return (
    <div className="run-telemetry" role="group" aria-label="Run telemetry">
      <span className="run-telemetry-cell run-telemetry-unit" title={cycleId ?? ""}>
        <span className="run-telemetry-label">unit</span>
        <code>{shortCycleId(cycleId)}</code>
      </span>
      <span className="run-telemetry-cell">
        <span className="run-telemetry-label">round</span>
        <strong>{round != null ? `R${round}` : "—"}</strong>
        {phase ? <span className="run-telemetry-sub">· {phase}</span> : null}
      </span>
      <span className="run-telemetry-cell">
        <span className="run-telemetry-label">best</span>
        <strong>{fmtPct1(best)}</strong>
        {delta != null && origin != null ? (
          <span className={`run-telemetry-delta ${deltaCls}`}>
            {deltaSign}
            {(delta * 100).toFixed(1)}% vs origin
          </span>
        ) : null}
      </span>
      <span className="run-telemetry-cell" title={spend.tooltip}>
        <span className="run-telemetry-label">spend</span>
        <strong>{spend.chip}</strong>
      </span>
      {lastQuery ? (
        <span className="run-telemetry-cell">
          <span className="run-telemetry-label">last</span>
          <strong>{lastQuery}</strong>
        </span>
      ) : null}
      <span className="run-telemetry-cell run-telemetry-updated">
        <span className="run-telemetry-label">updated</span>
        <strong>{ageText(dash?.wallclock_serialized_at)}</strong>
      </span>
      {campaignId && cycleId && isLive ? (
        <StopButton campaignId={campaignId} cycleId={cycleId} isLive={isLive} />
      ) : null}
    </div>
  );
}

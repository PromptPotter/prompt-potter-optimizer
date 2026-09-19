"use client";
import { useMemo } from "react";
import { Badge, CopyButton } from "@/components/ui";
import { CampaignSwitcher } from "@/components/shell/CampaignSwitcher";
import { ViewTabs } from "@/components/shell/ViewTabs";
import {
  buildForest,
  campaignLineParts,
  campaignTitle,
  fitnessTrend,
  headlineStats,
  readSpend,
} from "@/lib/derivations";
import { fmtPct0, fmtUsdCents } from "@/lib/format";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { pathLeaf } from "@/lib/ids";
import { cx } from "@/lib/cx";
import { runPhaseLabel } from "@/lib/run-phase";
import { useWorkspace } from "@/lib/workspace";
import type { Tab } from "@/lib/view-tab";

// The unit's masthead — three rows and the view strip, ONE header over every tab (chrome), so
// no two can grow different answers to "what am I looking at". Who this campaign is, what it
// runs with, and how it is doing; the strip sits in `main` rather than inside a pane's scroller,
// because it is the only way to switch views and one that scrolls out of reach is no nav. The
// band is full-bleed; the inner box centres on --dash-narrow-max, the one method the chat column
// and the dashboard spine also use (masthead.css).
export function RunMasthead({
  tab,
  onSelectTab,
  onFollowed,
}: {
  tab: Tab;
  onSelectTab: (t: Tab) => void;
  // Called after the operator follows the active run, so the shell can switch to the Dashboard
  // — same contract as the JobsDock's `onPicked`.
  onFollowed: () => void;
}) {
  const { campaignId, leafCycleId, viewedPath, campaigns, cycles, following, followActive } =
    useWorkspace();
  const { dash, dashRound } = useDashboard();

  // ONE forest for the header and its switcher, the same builder the sidebar reads.
  const origins = useMemo(() => buildForest(campaigns, cycles), [campaigns, cycles]);
  const run = useMemo(
    () =>
      origins
        .flatMap((o) => o.runs)
        .find((r) => r.campaign.campaign_id === campaignId) ?? null,
    [origins, campaignId],
  );

  // Sparkline: running-best composite over rounds, read from the dashboard's per-round summary
  // block. Keyed on `dash?.rounds` so a per-sample tick does not re-trace the path.
  const spark = useMemo(() => {
    const { best: ys } = fitnessTrend(dash?.rounds, dash?.best);
    if (ys.length < 2) return null;
    const W = 120;
    const H = 26;
    const maxY = Math.max(...ys, 0.01);
    const toX = (i: number) => (i / (ys.length - 1)) * W;
    const toY = (v: number) => H - 2 - (v / maxY) * (H - 4);
    const path = ys
      .map((y, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(y).toFixed(1)}`)
      .join("");
    return { path, area: `${path} L${W},${H} L0,${H} Z`, W, H };
  }, [dash?.rounds, dash?.best]);

  const title = run ? campaignTitle(run.campaign) : null;
  // Depth > 1 ⇒ viewing an inner descendant (an L4 inner loop). Backing out is the remote's
  // drill button, which owns that navigation; this only says where the view is.
  const innerLeaf = viewedPath && viewedPath.length > 1 ? pathLeaf(viewedPath) : null;

  const runPhase = dash?.run_phase ?? null;
  const phaseLabel = runPhaseLabel(runPhase, dash?.stop_reason);
  const phaseBody = (
    <>
      <span className="phase-dot" aria-hidden="true" />
      {phaseLabel}
    </>
  );

  const { best } = headlineStats(dash);
  // The candidate currently being scored ("C3.2"). `dash.candidate` is "C3.2/4" and goes stale
  // between rounds, so it stands in for the round only while the scorer is the active node.
  const scoringCand =
    dash?.current_round.active_node === "l1_score"
      ? String(dash.candidate || "").split("/")[0]
      : "";
  const roundsCap = dash?.run_limits?.max_rounds ?? null;
  const position = scoringCand || (dashRound != null ? `R${dashRound}` : "—");

  // One parser for spend, ceilings included — `readSpend` reads the armed `run_limits` pair,
  // which is the halt gate's own source.
  const { usedUsd, budgetUsd, unpricedTokens } = readSpend(dash);
  const spendFloor = unpricedTokens > 0 ? "≥" : "";

  return (
    <header className="run-header">
      <div className="run-header-inner">
        <div className="run-title">
          {title && <Badge>{title.dataset}</Badge>}
          {title?.label && <span className="run-name">{title.label}</span>}
          {title?.suffix && <span className="run-suffix">__{title.suffix}</span>}
          <CampaignSwitcher origins={origins} />
          {innerLeaf && (
            <span className="inner-breadcrumb">
              <span className="inner-breadcrumb-sep" aria-hidden="true">
                ⤷
              </span>
              <span className="inner-breadcrumb-label" title={innerLeaf.campaignId}>
                inner: {innerLeaf.campaignId}
              </span>
            </span>
          )}
          {leafCycleId && (
            <span className="run-id">
              ID: {leafCycleId}
              <CopyButton data={leafCycleId} title="Copy this cycle's id" />
            </span>
          )}
        </div>
        {run && <div className="run-setup">{campaignLineParts(run).join(" · ")}</div>}
        {/* Every chip reads `dash` for the VIEWED LEAF, the one per-cycle source. */}
        <div className="run-chips">
          {following ? (
            <span className={cx("chip", `phase-${runPhase}`)}>
              <span className="chip-lbl">State</span>
              {phaseBody}
            </span>
          ) : (
            <button
              type="button"
              className={cx("chip", "chip-btn", `phase-${runPhase}`)}
              onClick={() => {
                followActive();
                onFollowed();
              }}
              aria-label={`${phaseLabel} — pinned to this campaign. Follow the campaign the CLI is currently running.`}
              title="Pinned to this campaign. Click to follow the campaign the CLI is currently running."
            >
              <span className="chip-lbl">Follow</span>
              {phaseBody}
            </button>
          )}
          <span className="chip">
            <span className="chip-lbl">Best</span>
            {fmtPct0(best)}
            {spark && (
              <svg
                className="run-spark"
                viewBox={`0 0 ${spark.W} ${spark.H}`}
                aria-hidden="true"
              >
                <path className="area" d={spark.area} />
                <path className="line" d={spark.path} />
              </svg>
            )}
          </span>
          <span className="chip">
            <span className="chip-lbl">Rounds</span>
            {position}
            {roundsCap != null && <span className="chip-of"> / {roundsCap}</span>}
          </span>
          <span className={cx("chip", unpricedTokens > 0 && "chip-warn")}>
            <span className="chip-lbl">Spend</span>
            {usedUsd != null ? `${spendFloor}${fmtUsdCents(usedUsd)}` : "—"}
            {budgetUsd != null && (
              <span className="chip-of"> / {fmtUsdCents(budgetUsd)} cap</span>
            )}
          </span>
        </div>
        <ViewTabs tab={tab} onSelect={onSelectTab} />
      </div>
    </header>
  );
}

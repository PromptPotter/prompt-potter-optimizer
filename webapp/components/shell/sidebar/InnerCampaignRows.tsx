"use client";
// The inner-loop fan-out of one L4 (`promptpotter-self`) outer cycle, rendered
// as sidebar rows under the outer campaign (mounted only while its `↻ inner`
// chip is open). Rows are numbered in launch order. Selecting one pins the
// 2-hop path [outer, inner]: the dashboard re-roots to that inner cycle while
// chat and selection stay on the outer (root) thread.

import { useInnerCycles } from "@/lib/hooks/useInnerCycles";
import { useWorkspace } from "@/lib/workspace";
import { pathLeaf } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { runPhaseLabel } from "@/lib/run-phase";
import { cx } from "@/lib/cx";

export function InnerCampaignRows({
  outerCampaignId,
  outerCycleId,
}: {
  outerCampaignId: string;
  outerCycleId: string;
}) {
  const { viewedPath, selectCyclePath } = useWorkspace();
  const { inner, activeInnerCampaignId, activeInnerCycleId, loaded } = useInnerCycles(
    outerCampaignId,
    outerCycleId,
    true,
  );
  // The viewed leaf when descended into an inner loop (depth > 1); marks the
  // selected row. Depth 1 = viewing the outer, no inner row selected.
  const viewedLeaf =
    viewedPath && viewedPath.length > 1 ? pathLeaf(viewedPath) : null;

  // Emits <li> rows only — the caller owns the wrapping <ul> so the disclosure
  // nests cleanly under the outer campaign row.
  if (!loaded) {
    return <li className="inner-library-empty">Loading inner loops…</li>;
  }
  if (inner.length === 0) {
    return <li className="inner-library-empty">No inner campaigns yet</li>;
  }

  const rows = [...inner].sort((a, b) => (a.created_at < b.created_at ? -1 : 1));

  return (
    <>
      {rows.map((c, i) => {
        const isLive =
          c.campaign_id === activeInnerCampaignId && c.cycle_id === activeInnerCycleId;
        const selected =
          viewedLeaf?.campaignId === c.campaign_id &&
          viewedLeaf.cycleId === c.cycle_id;
        const statusLabel =
          !isLive && c.run_phase === "terminal"
            ? runPhaseLabel(c.run_phase, c.status)
            : null;
        return (
          <li key={`${c.campaign_id}\x1f${c.cycle_id}`}>
            <button
              type="button"
              className={cx("unit-library-item", "unit-library-child", selected && "selected")}
              onClick={() =>
                selectCyclePath([
                  { campaignId: outerCampaignId, cycleId: outerCycleId },
                  { campaignId: c.campaign_id, cycleId: c.cycle_id },
                ])
              }
              aria-current={selected ? "true" : undefined}
              title={`inner: ${c.campaign_id} · ${c.cycle_id}`}
            >
              <span className="unit-library-row">
                <span className="unit-library-name">
                  run {i + 1}
                  {isLive && (
                    <span className="unit-library-live" title="Inner loop running">
                      ●
                    </span>
                  )}
                </span>
                <span className="unit-library-meta">
                  {statusLabel && (
                    <>
                      <span className="unit-library-status">{statusLabel}</span>
                      {" · "}
                    </>
                  )}
                  {fmtPct0(c.best_accuracy)}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </>
  );
}

"use client";
// The inner-loop fan-out of one L4 (`promptpotter-self`) outer cycle, rendered
// as sidebar rows nested under the outer campaign. Self-fetches via
// useInnerCycles (only mounted while the outer row's inner disclosure is open)
// and reads the workspace's `viewedPath` + `selectCyclePath` directly, so the
// change stays local to the sidebar. Selecting a row pins the 2-hop path
// [outer, inner]: the dashboard re-roots to that inner cycle while chat and
// selection stay on the outer (root) thread.

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

  return (
    <>
      {inner.map((c) => {
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
            <div className={cx("unit-library-family", selected && "selected")}>
              <span className="unit-library-twist" aria-hidden="true" />
              <button
                type="button"
                className="unit-library-item"
                onClick={() =>
                  selectCyclePath([
                    { campaignId: outerCampaignId, cycleId: outerCycleId },
                    { campaignId: c.campaign_id, cycleId: c.cycle_id },
                  ])
                }
                aria-current={selected ? "true" : undefined}
                title={`inner: ${c.campaign_id} · ${c.cycle_id}`}
              >
                <span className="unit-library-mark">{isLive ? "●" : ""}</span>
                <span className="unit-library-row">
                  <span className="unit-library-name">
                    {c.campaign_id}
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
            </div>
          </li>
        );
      })}
    </>
  );
}

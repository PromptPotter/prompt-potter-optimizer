"use client";
import { memo, type CSSProperties } from "react";
import { sessionIndexOf, shortFamilyTail } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { CardFrame, CopyButton } from "@/components/ui";
import { Forest } from "./Forest";
import { CleanupConfirmModal } from "./CleanupConfirmModal";
import { RotatePrompt } from "@/components/shell/RotatePrompt";
import { useLineage } from "./useLineage";
import { useLineageOverlay } from "@/lib/lineage-overlay";

// The lineage card — presentational. All data, expand state, and the cleanup
// mutation live in useLineage; this renders the card chrome, the fixed-height
// resizable viewport, and one Forest per session. The in-view (campaignId,
// cycleId) + the `selectCycle` jump come from the workspace; the in-view
// cycle's live `best` (for the inherited-fork empty state) reads off the
// dashboard stream. No props — every input is self-sourced from context.
export const FamilyTree = memo(function FamilyTree() {
  const { dash } = useDashboard();
  const { campaignId, cycleId, selectCycle: onSelectCycle } = useWorkspace();
  const {
    forests,
    detailByCycle,
    expanded,
    onLaneActivate,
    naturalWidth,
    multiSession,
    totalDescendants,
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
  } = useLineage({ campaignId, cycleId });
  // Lens/mask overlay read straight from its provider — the same single source
  // Forest and the fitness panel render, not laundered through useLineage.
  const { lens, setLens, maskActive, maskLabel, whatifActive } = useLineageOverlay();

  return (
    <CardFrame
      className="lineage-card"
      style={
        naturalWidth > 0
          ? ({ "--lineage-natural-w": `${naturalWidth}px` } as CSSProperties)
          : undefined
      }
      title={
        <span className="lineage-title">
          Lineage
          {maskActive && (
            <span className="lineage-mask-tag" title={`Showing the ${maskLabel} mask — divergence vs the realized record`}>
              {maskLabel}
            </span>
          )}
        </span>
      }
      actions={
        <span className="family-cladogram-head-meta">
          {/* Lens: re-project the record under an alternative criterion and mark
              where it would have forked the realized lineage. Backend projection;
              this only selects which served overlay to render. */}
          <label
            className="lineage-lens"
            title={
              whatifActive
                ? "What-If is driving the lineage — adjust the evaluator cards/weights, or close What-If to use a preset lens"
                : "Re-project the lineage under an alternative criterion and mark where it would have diverged"
            }
          >
            <span className="lineage-lens-label">Lens</span>
            <select
              className="lineage-lens-select"
              value={whatifActive ? "whatif" : lens}
              disabled={whatifActive}
              onChange={(e) => setLens(e.target.value)}
              aria-label="Lineage lens — mark divergence under an alternative scoring or abort criterion"
            >
              {/* While What-If is open it is the master lens (chip-driven); the
                  preset dropdown is disabled and shows the live What-If state. */}
              {whatifActive && <option value="whatif">What-If (live)</option>}
              <option value="">Realized</option>
              <optgroup label="Scoring">
                <option value="score:accuracy">Accuracy</option>
              </optgroup>
              <optgroup label="Abort off">
                <option value="abort:epsilon_off">No ε-elimination</option>
                <option value="abort:lock_in_off">No lock-in</option>
                <option value="abort:all_off">No early abort</option>
              </optgroup>
            </select>
          </label>
          <span className="badge">
            {totalDescendants} {totalDescendants === 1 ? "descendant" : "descendants"}
          </span>
          {cleanup.stubCount > 0 && (
            <button
              type="button"
              className="family-cladogram-cleanup-btn"
              onClick={cleanup.request}
              title="Delete every empty-stub fork in this campaign from disk"
            >
              Clean up {cleanup.stubCount} stub{cleanup.stubCount === 1 ? "" : "s"}
            </button>
          )}
          {cleanup.acked && cleanup.stubCount === 0 && (
            <span className="family-cladogram-cleanup-done" title="Last cleanup result">
              cleaned
            </span>
          )}
          <CopyButton data={forests} title="Copy lineage as JSON" />
        </span>
      }
    >
      <RotatePrompt surfaceName="The lineage tree" skipRender>
        <section className="family-cladogram" aria-label="Campaign lineage tree">
          {/* One fixed-height, operator-resizable viewport for every session
              forest. Keyed on campaignId so a campaign switch remounts it: the
              dragged height + scroll reset to the default instead of leaking
              into the next campaign. Rounds extend rightward (horizontal scroll),
              tall stacks scroll vertically; the grip drags the box taller. */}
          <div key={campaignId ?? "none"} className="family-cladogram-viewport">
            {forests.map((f) => (
              <Forest
                key={f.rootId}
                tree={f.tree}
                campaignId={campaignId ?? ""}
                cycleId={cycleId}
                detailByCycle={detailByCycle}
                expanded={expanded}
                onLaneActivate={onLaneActivate}
                onSelectCycle={onSelectCycle}
                sessionLabel={
                  multiSession ? `Session ${sessionIndexOf(f.rootId)}` : null
                }
              />
            ))}
          </div>
          {!viewedHasRounds && (
            <div className="lineage-empty">
              {isInheritedSibling && parentId ? (
                <>
                  inherited from{" "}
                  {campaignId ? (
                    <button
                      type="button"
                      className="lineage-inherit-link"
                      onClick={() => onSelectCycle(campaignId, parentId)}
                      title={`Switch to ${parentId}`}
                    >
                      {shortFamilyTail(parentId) || parentId}
                    </button>
                  ) : (
                    <span>{shortFamilyTail(parentId) || parentId}</span>
                  )}
                  {dash?.best != null ? ` · best ${fmtPct0(dash.best)}` : ""}
                  {" · no new rounds yet"}
                </>
              ) : (
                "No rounds on disk yet — the tree appears once round 1 lands."
              )}
            </div>
          )}
          {cleanup.open && (
            <CleanupConfirmModal
              stubCount={cleanup.stubCount}
              cleaning={cleanup.cleaning}
              error={cleanup.error}
              onCancel={cleanup.cancel}
              onConfirm={cleanup.confirm}
            />
          )}
        </section>
      </RotatePrompt>
    </CardFrame>
  );
});

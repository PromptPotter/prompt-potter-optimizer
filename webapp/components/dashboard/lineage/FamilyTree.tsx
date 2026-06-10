"use client";
import { memo, type CSSProperties } from "react";
import type { DashboardSnapshot } from "@/lib/poll";
import { sessionIndexOf, shortFamilyTail } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { CardFrame } from "@/components/ui/Card";
import { Forest } from "./Forest";
import { CleanupConfirmModal } from "./CleanupConfirmModal";
import { RotatePrompt } from "@/components/shell/RotatePrompt";
import { useLineage } from "./useLineage";

interface Props {
  // Live dashboard for the IN-VIEW cycle. Used to override that one cycle's
  // expanded candidate detail with live (2 s, in-flight) data — every other
  // cycle reads the fetched lineage snapshot. Source-by-cycle-role, never a
  // per-field merge of the two (the banned stitch).
  dash: DashboardSnapshot | null;
  // The campaign whose lineage to render. A campaign is a FOREST: it holds
  // N session roots, each with its own fork tree. One fetch returns every
  // cycle; we render one cladogram per session.
  campaignId: string | null;
  // The cycle currently in view — its lane is the one that expands into the
  // intra-cycle candidate cladogram (every other lane stays compact), and it's
  // the lane that gets the live `dash` override.
  cycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}

// The lineage card — presentational. All data, expand state, the live-dashboard
// overlay, and the cleanup mutation live in useLineage; this renders the card
// chrome, the fixed-height resizable viewport, and one Forest per session.
export const FamilyTree = memo(function FamilyTree({
  dash,
  campaignId,
  cycleId,
  onSelectCycle,
}: Props) {
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
    mask,
    setMask,
    divergenceByKey,
    divergentKeys,
  } = useLineage({ dash, campaignId, cycleId });

  return (
    <CardFrame
      className="lineage-card"
      style={
        naturalWidth > 0
          ? ({ "--lineage-natural-w": `${naturalWidth}px` } as CSSProperties)
          : undefined
      }
      title={<span>Lineage</span>}
      actions={
        <span className="family-cladogram-head-meta">
          {/* Scoring-lens (mask): re-score the record under an alternative formula
              and mark where it would have forked the realized lineage. Backend
              projection; this only selects which served overlay to render. */}
          <label className="lineage-lens" title="Re-score the lineage under an alternative scoring formula and mark where it would have diverged">
            <span className="lineage-lens-label">Lens</span>
            <select
              className="lineage-lens-select"
              value={mask ?? ""}
              onChange={(e) => setMask(e.target.value || null)}
              aria-label="Scoring lens — mark lineage divergence under an alternative formula"
            >
              <option value="">Realized</option>
              <option value="accuracy">Accuracy</option>
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
                divergenceByKey={divergenceByKey}
                divergentKeys={divergentKeys}
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

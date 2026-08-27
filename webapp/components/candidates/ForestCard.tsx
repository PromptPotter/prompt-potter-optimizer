"use client";
import { useMemo } from "react";
import { CardFrame, Chip, CopyButton, Toolbar, ToolbarSpacer } from "@/components/ui";
import { RotatePrompt } from "@/components/shell/RotatePrompt";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useSelection } from "@/lib/SelectionContext";
import { isSelectedCandidate } from "@/lib/types";
import { selectedCandidateOf } from "@/lib/derivations";
import { encodeCyclePath, pathLeaf, shortFamilyTail } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { CleanupConfirmModal } from "./CleanupConfirmModal";
import { Forest, type CladogramCtx } from "./Forest";
import { ROOMY } from "./forest-layout";
import { IconBroom } from "./toolbar-icons";
import { useLineage } from "./useLineage";

// The lineage forest — its OWN card, not a section inside the candidates card.
//
// It was briefly nested in there, and that was wrong for a structural reason: the
// candidates card's whole geometry exists to keep the dendrogram aligned to the
// bars, so everything inside it is bound to the chart's box. The forest has no
// business being aligned to anything — it is a cladogram of CYCLES, on its own
// round-column grid, and it wants whatever width and height it needs. Two things
// that share no axis should not share a box.
//
// Self-sourced, like every other card: `useLineage` owns the tree, the value
// overlays and the cleanup mutation; the toggle that opens this card lives beside
// the dendrogram (the thing it's the counterpart to) and writes `showForest`.
export function ForestCard() {
  const { dash } = useDashboard();
  const {
    campaignId,
    cycleId,
    viewedPath,
    selectCycle: onSelectCycle,
    selectCyclePath,
  } = useWorkspace();
  const { candidate, setSelectionForCandidate } = useSelection();
  const {
    tree,
    valueByKey,
    thetaByKey,
    metric,
    expanded,
    onLaneActivate,
    setShowForest,
    totalDescendants,
    viewedHasRounds,
    isInheritedSibling,
    parentId,
    cleanup,
  } = useLineage({
    campaignId,
    cycleId,
    path: viewedPath,
    electedMetric: dash?.headline_metric ?? "accuracy",
  });

  // On the dashboard a searchpoint click INSPECTS it. A node in a non-selected lane also
  // navigates there, so the inspector and samples follow it — but it SELECTS either way: the
  // selection names the course being navigated to, so the provider's cycle-change clear keeps
  // it. This used to navigate and drop the pick on the floor.
  //
  // Navigation rides the node's OWN `coursePath`. Rebuilding an address as
  // `(campaignId, n.cycleId)` names the wrong run inside an `.inner/` sandbox, where cycle
  // ids repeat.
  const ctx = useMemo<CladogramCtx>(
    () => ({
      viewedKey: viewedPath ? encodeCyclePath(viewedPath) : null,
      // No comparison and no cut: this cladogram maps ONE campaign whole, for the operator
      // watching it run. Both belong to the surface that puts several points side by side.
      channels: [],
      clip: null,
      isPicked: (n) =>
        isSelectedCandidate(candidate, pathLeaf(n.coursePath).cycleId, n.round, n.candidateId),
      onPickCandidate: (n, value) => {
        const nodeCycleId = pathLeaf(n.coursePath).cycleId;
        if (n.coursePathKey !== (viewedPath ? encodeCyclePath(viewedPath) : null)) {
          selectCyclePath(n.coursePath, null);
        }
        setSelectionForCandidate(
          isSelectedCandidate(candidate, nodeCycleId, n.round, n.candidateId)
            ? null
            : // The value is the DRAWING's — whichever metric this cladogram is inked with —
              // while everything else about the point comes off the served node it was placed
              // from, `selectedCandidateOf` included: the label rule lives there now.
              selectedCandidateOf(n.node, nodeCycleId, value),
        );
      },
    }),
    [viewedPath, candidate, selectCyclePath, setSelectionForCandidate],
  );

  return (
    <CardFrame
      className="forest-card"
      title={
        <Toolbar>
          <span className="cand-title">Lineage</span>
          <span className="badge">
            {totalDescendants} {totalDescendants === 1 ? "descendant" : "descendants"}
          </span>
          <ToolbarSpacer />
          {cleanup.stubCount > 0 && (
            <Chip
              icon
              on={false}
              ariaLabel={`Clean up ${cleanup.stubCount} empty-stub fork${cleanup.stubCount === 1 ? "" : "s"}`}
              title={`Delete ${cleanup.stubCount} empty-stub fork${cleanup.stubCount === 1 ? "" : "s"} from disk`}
              onClick={cleanup.request}
            >
              <IconBroom />
            </Chip>
          )}
          <CopyButton data={tree} title="Copy the lineage as JSON" />
          <button
            type="button"
            className="forest-close"
            aria-label="Close the lineage forest"
            title="Close"
            onClick={() => setShowForest(false)}
          >
            ×
          </button>
        </Toolbar>
      }
    >
      <RotatePrompt surfaceName="The lineage forest" skipRender>
        <section className="family-cladogram" aria-label="Campaign lineage tree">
          {/* One fixed-height, operator-resizable viewport for the campaign's tree.
              Keyed on campaignId so a campaign switch remounts it: the dragged
              height + scroll reset to the default instead of leaking into the next
              campaign. */}
          <div key={campaignId ?? "none"} className="family-cladogram-viewport">
            {tree && (
              <Forest
                tree={tree}
                valueByKey={valueByKey}
                thetaByKey={thetaByKey}
                metric={metric}
                expanded={expanded}
                onLaneActivate={onLaneActivate}
                ctx={ctx}
                d={ROOMY}
              />
            )}
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
}

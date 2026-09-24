"use client";
import { useMemo } from "react";
import {
  Badge,
  CardFrame,
  Chip,
  CopyButton,
  IconBroom,
  Toolbar,
  ToolbarSpacer,
} from "@/components/ui";
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
import { useLineage } from "./useLineage";

// The lineage forest card: a cladogram of cycles, sharing no axis with the candidates card's bars.
// The toggle opening it lives beside the dendrogram and writes `showForest`.
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

  // Navigate on the node's OWN `coursePath`: `(campaignId, n.cycleId)` names the wrong run inside an
  // `.inner/` sandbox, where cycle ids repeat.
  const ctx = useMemo<CladogramCtx>(
    () => ({
      viewedKey: viewedPath ? encodeCyclePath(viewedPath) : null,
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
            : selectedCandidateOf(n.node, nodeCycleId, value),
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
          <Badge>
            {totalDescendants} {totalDescendants === 1 ? "descendant" : "descendants"}
          </Badge>
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
      <RotatePrompt surfaceName="The lineage forest">
        <section className="family-cladogram" aria-label="Campaign lineage tree">
          {/* Keyed on campaignId so the dragged height and scroll reset on a campaign switch. */}
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

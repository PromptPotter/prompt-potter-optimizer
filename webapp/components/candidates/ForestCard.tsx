"use client";
import { useMemo } from "react";
import type { ServedDashboard } from "@/lib/api";
import {
  Badge,
  CardFrame,
  Chip,
  CopyButton,
  Empty,
  IconBroom,
  Loading,
  Toolbar,
  ToolbarSpacer,
} from "@/components/ui";
import { RotatePrompt } from "@/components/shell/RotatePrompt";
import { DASHBOARD_PENDING, useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { useSelection } from "@/lib/SelectionContext";
import { isSelectedCandidate } from "@/lib/types";
import { selectedNodeOf } from "@/lib/derivations";
import { encodeCyclePath, pathLeaf, shortFamilyTail } from "@/lib/ids";
import { CleanupConfirmModal } from "./CleanupConfirmModal";
import { Forest, type CladogramCtx } from "./Forest";
import { ROOMY } from "./forest-layout";
import { useLineage } from "@/lib/hooks/useLineage";
import { useViewedCourse } from "@/lib/lineage";

export function ForestCard() {
  const { dash } = useCycleStream();
  if (!dash) {
    return (
      <CardFrame className="forest-card" title={<span className="cand-title">Lineage</span>}>
        <Loading className="lineage-empty">{DASHBOARD_PENDING}</Loading>
      </CardFrame>
    );
  }
  return <ReadForestCard dash={dash} />;
}

function ReadForestCard({ dash }: { dash: ServedDashboard }) {
  const {
    campaignId,
    cycleId,
    viewedPath,
    navigate,
  } = useWorkspace();
  const { candidate, setSelectionForCandidate } = useSelection();
  const selection = useViewedCourse()?.facts.standing?.selection ?? null;
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
    electedMetric: dash.display_metric,
  });

  const ctx = useMemo<CladogramCtx>(
    () => ({
      viewedKey: viewedPath ? encodeCyclePath(viewedPath) : null,
      channels: [],
      clip: null,
      isPicked: (n) => {
        const own = selectedNodeOf(n.node, pathLeaf(n.coursePath).cycleId);
        return isSelectedCandidate(candidate, own.cycle_id, own.round, own.arm);
      },
      onPickCandidate: (n) => {
        const own = selectedNodeOf(n.node, pathLeaf(n.coursePath).cycleId);
        if (n.coursePathKey !== (viewedPath ? encodeCyclePath(viewedPath) : null)) {
          navigate(n.coursePath);
        }
        setSelectionForCandidate(
          isSelectedCandidate(candidate, own.cycle_id, own.round, own.arm) ? null : own,
        );
      },
    }),
    [viewedPath, candidate, navigate, setSelectionForCandidate],
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
          {cleanup.stubCount !== null && cleanup.stubCount > 0 && (
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
            <Empty className="lineage-empty">
              {isInheritedSibling && parentId ? (
                <>
                  inherited from{" "}
                  {campaignId ? (
                    <button
                      type="button"
                      className="lineage-inherit-link"
                      onClick={() => navigate([{ campaignId, cycleId: parentId }])}
                      title={`Switch to ${parentId}`}
                    >
                      {shortFamilyTail(parentId) || parentId}
                    </button>
                  ) : (
                    <span>{shortFamilyTail(parentId) || parentId}</span>
                  )}
                  {selection ? ` · selection ${selection.label}` : ""}
                  {" · no new rounds yet"}
                </>
              ) : (
                "No rounds on disk yet — the tree appears once round 1 lands."
              )}
            </Empty>
          )}
          {cleanup.open && cleanup.stubCount !== null && (
            <CleanupConfirmModal
              stubCount={cleanup.stubCount}
              cleaning={cleanup.cleaning}
              failure={cleanup.failure}
              onCancel={cleanup.cancel}
              onConfirm={cleanup.confirm}
            />
          )}
        </section>
      </RotatePrompt>
    </CardFrame>
  );
}

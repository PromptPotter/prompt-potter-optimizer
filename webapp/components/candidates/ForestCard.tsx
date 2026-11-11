import { useMemo } from "react";
import type { ServedDashboard } from "@/lib/api/types";
import {
  Badge,
  CardFrame,
  Chip,
  CopyButton,
  Empty,
  FailureLine,
  IconBroom,
  Loading,
  Toolbar,
  ToolbarSpacer,
} from "@/components/ui";
import { DASHBOARD_PENDING, useViewedDashboard } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { pickOf } from "@/lib/derivations";
import { shortFamilyTail } from "@/lib/ids";
import { CleanupConfirmModal } from "./CleanupConfirmModal";
import { Forest, type CladogramCtx } from "./Forest";
import { useCampaignForest } from "@/lib/hooks/useCampaignForest";
import { useViewedCourse } from "@/lib/lineage";

export function ForestCard() {
  const { dash } = useViewedDashboard();
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
  const { viewed, pick, pickCandidate, navigate } = useWorkspace();
  const campaignId = viewed?.root.campaignId ?? null;
  const cycleId = viewed?.root.cycleId ?? null;
  const viewedPath = viewed?.path ?? null;
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
    deleteViewed,
    deleting,
    deleteFailure,
  } = useCampaignForest({
    campaignId,
    cycleId,
    path: viewedPath,
    displayMetric: dash.display_metric,
  });

  const ctx = useMemo<CladogramCtx>(
    () => ({
      viewedKey: viewed?.address ?? null,
      channels: [],
      clip: null,
      isPicked: (n) => n.node.address === pick?.address,
      onPickCandidate: (n) =>
        n.node.address === pick?.address
          ? pickCandidate(null)
          : pickCandidate(pickOf(n.node), n.coursePath),
    }),
    [viewed, pick, pickCandidate],
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
                    onClick={() => navigate({ path: [{ campaignId, cycleId: parentId }] })}
                    title={`Switch to ${parentId}`}
                  >
                    {shortFamilyTail(parentId) || parentId}
                  </button>
                ) : (
                  <span>{shortFamilyTail(parentId) || parentId}</span>
                )}
                {selection ? ` · selection ${selection.label}` : ""}
                {" · no new rounds yet"}
                {deleteViewed && campaignId ? (
                  <>
                    {" · "}
                    <button
                      type="button"
                      className="lineage-inherit-link"
                      disabled={deleting}
                      onClick={() =>
                        deleteViewed(() => navigate({ path: [{ campaignId, cycleId: parentId }] }))
                      }
                    >
                      {deleting ? "deleting…" : "delete this empty fork"}
                    </button>
                  </>
                ) : null}
              </>
            ) : (
              "No rounds on disk yet — the tree appears once round 1 lands."
            )}
          </Empty>
        )}
        <FailureLine failure={deleteFailure} />
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
    </CardFrame>
  );
}

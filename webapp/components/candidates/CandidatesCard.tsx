"use client";
import type { CSSProperties } from "react";
import { FitnessChart } from "./FitnessChart";
import { DendrogramStrip } from "./DendrogramStrip";
import { ThetaCaveatNotice } from "./AbilityInfo";
import { CandidatesToolbar } from "./CandidatesToolbar";
import { useCandidatesModel, type CandidatesModel } from "./useCandidatesModel";
import { CardFrame, Chip, IconTree } from "@/components/ui";
import { Criterion } from "@/components/shell/scoring/Criterion";
import { ApplyScenarioPanel } from "@/components/candidates/ApplyScenarioPanel";
import { setScoringMask } from "@/lib/scoring-mask";
import { FitnessRankSummary } from "./FitnessRankSummary";
import { SampleSetControl } from "./SampleSetControl";
import { cx } from "@/lib/cx";
import { useCycleStream, type DashboardSnapshot } from "@/lib/poll";

function ForestToggle({ model: m }: { model: CandidatesModel }) {
  const { showForest, totalDescendants } = m;
  return (
    <Chip
      icon={totalDescendants === 0}
      on={showForest}
      ariaLabel={
        showForest
          ? "Hide the lineage forest"
          : `Show the lineage forest — the full campaign tree, ${totalDescendants} descendant${totalDescendants === 1 ? "" : "s"}`
      }
      title={`${showForest ? "Hide" : "Show"} the campaign tree — every cycle and fork side by side (${totalDescendants} descendant${totalDescendants === 1 ? "" : "s"})`}
      onClick={() => m.setShowForest(!showForest)}
    >
      <span className="cand-forest-toggle">
        <IconTree />
        {totalDescendants > 0 && <span className="cand-view-count">{totalDescendants}</span>}
      </span>
    </Chip>
  );
}

function FitnessLegend({ model: m }: { model: CandidatesModel }) {
  const { legend, seriesCtx } = m;
  if (legend.length === 0) return null;
  return (
    <div className="fitness-legend">
      {legend.map((s) => (
        <span key={s.key} title={s.hint?.(seriesCtx)}>
          <span
            className={cx("swatch", s.kind === "line" && "line", s.hollow && "hollow")}
            style={{ "--ink": `var(${s.ink(seriesCtx)})` } as CSSProperties}
          />
          {s.legend?.(seriesCtx)}
        </span>
      ))}
    </div>
  );
}

export function CandidatesCard() {
  const { dash } = useCycleStream();
  if (!dash) {
    return (
      <CardFrame className="cand-card" title={<span className="cand-title">Candidates</span>}>
        <div className="lineage-empty">Waiting for this run&rsquo;s dashboard…</div>
      </CardFrame>
    );
  }
  return <ReadCandidatesCard dash={dash} />;
}

function ReadCandidatesCard({ dash }: { dash: DashboardSnapshot }) {
  const m = useCandidatesModel(dash);
  const { views, maskOpen, viewedCandidateId, areCourses, wonOnTheta } = m;

  return (
    <CardFrame
      className={cx(
        "cand-card",
        maskOpen && "mask-open",
      )}
      title={<CandidatesToolbar model={m} />}
    >
      <div className="fitness-body">
        {!areCourses && wonOnTheta && (
          <ThetaCaveatNotice caveat={m.ability?.caveat ?? null} ability={m.ability} />
        )}
        {!areCourses && wonOnTheta && m.floorPinned.length > 0 && (
          <>
            <ThetaCaveatNotice caveat="floor_pinned" />
            <div className="theta-caveat-arms">Affected: {m.floorPinned.join(", ")}.</div>
          </>
        )}
        {m.pickedSet && <SampleSetControl rounds={m.history} overlap={m.overlap} unit={m.unit} />}
        {/* The dendrogram's x-alignment depends on sharing this box with the canvas. */}
        <div className="fitness-chart-wrap">
          <FitnessLegend model={m} />
          <FitnessChart
            ctx={m.seriesCtx}
            divergenceBoundary={m.divergenceBoundary}
            inFlightIndex={m.inFlightIndex}
            selectedKey={m.selectedKey}
            onSelect={m.onSelect}
            onGeometry={m.onGeometry}
          />
          <div className="cand-tree-row">
            {!viewedCandidateId && (
              <DendrogramStrip
                views={views}
                plot={m.plot}
                metric={m.metric}
                selectedKey={m.selectedKey}
                onSelect={m.onSelect}
                forkedFrom={m.forkedFrom}
                onFreeHierarchy={m.onFreeHierarchy}
              />
            )}
            <ForestToggle model={m} />
          </div>
        </div>
        {maskOpen && !viewedCandidateId && (
          <Criterion
            className="fitness-mask"
            startRung={1}
            mask={m.mask}
            onMask={(next) => setScoringMask({ mask: next })}
            anchors={m.anchors}
            formula={m.lensCriterion}
            note="Read this branch under a criterion it was not scored on. Every cell the run recorded is re-graded and folded, as a run under it would — the elections are re-decided, never re-run."
            summary={
              <FitnessRankSummary
                views={views}
                shift={m.lensShift}
                criterion={m.criterionActive}
              />
            }
          />
        )}
        {maskOpen && !viewedCandidateId && (
          <ApplyScenarioPanel
            campaignId={m.campaignId}
            cycleId={m.cycleId}
            isLive={m.isLive}
            criterion={m.lensCriterion}
            divergentRound={m.divergentRound}
            nextRound={m.history.length}
          />
        )}
      </div>
    </CardFrame>
  );
}

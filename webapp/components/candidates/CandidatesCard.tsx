import type { CSSProperties } from "react";
import type { ServedDashboard } from "@/lib/api/types";
import { FitnessChart } from "./FitnessChart";
import { DendrogramStrip } from "./DendrogramStrip";
import { ThetaCaveats } from "./AbilityInfo";
import { CandidatesToolbar } from "./CandidatesToolbar";
import { useCandidatesModel, type CandidatesModel } from "./useCandidatesModel";
import { CardFrame, Chip, HoverCard, IconTree, Loading, ReadState } from "@/components/ui";
import { Criterion } from "@/components/criterion/Criterion";
import { ApplyScenarioPanel } from "@/components/candidates/ApplyScenarioPanel";
import { setScoringMask } from "@/lib/state/scoring-mask";
import { FitnessRankSummary } from "./FitnessRankSummary";
import { SampleSetControl } from "./SampleSetControl";
import { cx } from "@/lib/cx";
import { useViewedLineage } from "@/lib/state/lineage";
import { DASHBOARD_PENDING, useViewedDashboard } from "@/lib/state/poll";

// The one button between the tree's two layouts: paired under the bars, or freed as its own card.
export function TreePairing({
  freed,
  setFreed,
  descendants,
}: {
  freed: boolean;
  setFreed: (freed: boolean) => void;
  descendants: number;
}) {
  return (
    <Chip
      icon={descendants === 0}
      on={freed}
      ariaLabel={
        freed
          ? "Pair the lineage tree under the bars"
          : `Free the lineage tree from the bars — the full campaign tree, ${descendants} descendant${descendants === 1 ? "" : "s"}`
      }
      title={freed ? "Pair the tree under the bars" : "Free the tree from the bars"}
      onClick={() => setFreed(!freed)}
    >
      <span className="cand-forest-toggle">
        <IconTree />
        {descendants > 0 && <span className="cand-view-count">{descendants}</span>}
      </span>
    </Chip>
  );
}

function FitnessLegend({ model: m }: { model: CandidatesModel }) {
  const { legend, seriesCtx } = m;
  if (legend.length === 0) return null;
  return (
    <div className="fitness-legend">
      {legend.map((s) => {
        const hint = s.hint?.(seriesCtx);
        const item = (
          <>
            <span
              className={cx(
                "swatch",
                s.kind === "line" && "line",
                s.kind === "mark" && "mark",
                s.mark?.filled && "filled",
              )}
              style={{ "--ink": `var(${s.ink(seriesCtx)})` } as CSSProperties}
            />
            {s.legend?.(seriesCtx)}
          </>
        );
        return hint ? (
          <HoverCard key={s.key} content={hint} focusable>
            {item}
          </HoverCard>
        ) : (
          <span key={s.key}>{item}</span>
        );
      })}
    </div>
  );
}

// The bars are the lineage tree's rows, so the card waits on that read as well as the dashboard.
export function CandidatesCard() {
  const { dash } = useViewedDashboard();
  const { tree, treeRead } = useViewedLineage();
  if (dash && tree !== null) return <ReadCandidatesCard dash={dash} />;
  return (
    <CardFrame className="cand-card" title={<span className="cand-title">Candidates</span>}>
      {dash ? (
        <ReadState
          read={treeRead}
          subject={CANDIDATES}
          idle={<Loading className="lineage-empty">Reading {CANDIDATES}…</Loading>}
          className="lineage-empty"
        >
          {() => null}
        </ReadState>
      ) : (
        <Loading className="lineage-empty">{DASHBOARD_PENDING}</Loading>
      )}
    </CardFrame>
  );
}

const CANDIDATES = "this run’s candidates";

function ReadCandidatesCard({ dash }: { dash: ServedDashboard }) {
  const m = useCandidatesModel(dash);
  const { views, maskOpen, drilled } = m;

  return (
    <CardFrame
      className={cx(
        "cand-card",
        maskOpen && "mask-open",
      )}
      title={<CandidatesToolbar model={m} />}
    >
      <div className="fitness-body">
        {m.pickedSet && <SampleSetControl rounds={m.history} coverage={m.coverage} overlap={m.overlap} unit={m.unit} />}
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
            {!drilled && !m.treeFreed && (
              <DendrogramStrip
                views={views}
                plot={m.plot}
                metric={m.metric}
                pickedAddress={m.selectedKey}
                onSelect={m.onSelect}
                forkedFrom={m.forkedFrom}
                onFreeHierarchy={m.onFreeHierarchy}
              />
            )}
            <TreePairing
              freed={m.treeFreed}
              setFreed={m.setTreeFreed}
              descendants={m.totalDescendants}
            />
          </div>
          {!m.areCourses && m.wonOnTheta && (
            <div className="fitness-notes">
              <ThetaCaveats caveat={m.ability?.caveat ?? null} priorDominated={m.priorDominated} />
            </div>
          )}
        </div>
        {maskOpen && !drilled && (
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
        {maskOpen && !drilled && (
          <ApplyScenarioPanel
            path={m.viewedPath}
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

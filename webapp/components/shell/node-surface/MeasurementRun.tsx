"use client";
import { useMemo, useState, type ReactNode } from "react";
import { useSelection } from "@/lib/SelectionContext";
import { useLeafIsL4, useWorkspace } from "@/lib/workspace";
import { useRound } from "@/lib/hooks/useRound";
import { useViewedLineage } from "@/lib/lineage";
import {
  candidateSearchPoint,
  candidateVerdicts,
  innerPanelIndex,
  liveCandidateSearchPoint,
  liveCandidateVerdicts,
  panelCellKey,
  pathOf,
} from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import type { CourseNode } from "@/lib/api";
import { ARM_OUTCOMES_ENDED_EARLY } from "@/lib/api/types.generated";
import { useCycleStream } from "@/lib/poll";
import {
  isSelectedCandidate,
  selectedCandidateOf,
  type ArmRow,
  type SampleRow,
} from "@/lib/types";
import type { CandidateSearchPoint, CandidateVerdict } from "@/lib/derivations";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";
import { fmtPct0, unitCount, unitPlural } from "@/lib/format";
import { Badge, CopyButton, SegmentedControl, Term, type Segment } from "@/components/ui";
import { TERMS } from "@/lib/terms";
import { NodeSurface } from "./NodeSurface";
import { PanelCellRow } from "./PanelCellRow";

export function MeasurementRun({
  round,
}: {
  round: number;
}) {
  const { dash, isLive } = useCycleStream();
  const cv = useConnector();
  const { viewedPath, leafCycleId, drillInto } = useWorkspace();
  // The DECLARED backend type, not "the tree found inner runs": minting cells would tally as misses.
  const isL4 = useLeafIsL4();
  const { index } = useViewedLineage();
  // Null = not L4; empty = L4 with the sandbox not yet read, and the cells still list.
  const cells = useMemo(
    () => (isL4 ? innerPanelIndex(index, viewedPath) : null),
    [isL4, index, viewedPath],
  );
  const openRun = (run: CourseNode): void => {
    const at = pathOf(run).at(-1);
    if (at) drillInto(at.campaignId, at.cycleId);
  };
  const { setSelectionForCandidate, candidate: selected } = useSelection();
  const onSelectCandidate = (c: ArmRow | null): void => {
    const arm = c?.reading.arm;
    setSelectionForCandidate(
      arm && leafCycleId
        ? selectedCandidateOf(leafCycleId, arm.round, arm.candidate_id, arm.label)
        : null,
    );
  };
  const {
    unfiled: isLiveView,
    doc: roundDoc,
    loading: roundLoading,
    failure: roundFailure,
    rows: candidates,
    samples: samplesFor,
  } = useRound(viewedPath, round);

  const [candFilter, setCandFilter] = useState<string>("all");

  const verdicts = useMemo(
    () =>
      isLiveView ? liveCandidateVerdicts(dash) : candidateVerdicts(roundDoc?.candidate_scores ?? []),
    [isLiveView, dash, roundDoc],
  );

  const groups = useMemo(() => {
    const out: {
      candidate: ArmRow;
      samples: SampleRow[];
      spec: CandidateSearchPoint | null;
    }[] = [];
    for (const c of candidates) {
      const filtered = samplesFor(c);
      const { arm } = c.reading;
      const spec =
        c.source === "inflight"
          ? liveCandidateSearchPoint(dash, arm.label)
          : candidateSearchPoint(roundDoc, arm.candidate_id);
      out.push({ candidate: c, samples: filtered, spec });
    }
    if (candFilter !== "all") {
      return out.filter((g) => g.candidate.reading.arm.candidate_id === candFilter);
    }
    return out;
  }, [candidates, candFilter, dash, roundDoc, samplesFor]);

  const totalRows = useMemo(
    () => groups.reduce((n, g) => n + g.samples.length, 0),
    [groups],
  );

  const oneCandidate = candFilter !== "all";
  const picked = candidates.find((c) => c.reading.arm.candidate_id === candFilter);
  const narrowTo =
    picked && picked.source !== "inflight" ? picked.reading.arm.candidate_id : undefined;

  if (!dash || (!isLiveView && roundLoading)) {
    return <Region>Loading round {round}…</Region>;
  }
  const unit = dash.measured_unit;
  if (!isLiveView && roundFailure) {
    return <Region>Could not load round {round}.</Region>;
  }
  if (candidates.length === 0) {
    return (
      <Region>
        {isLiveView && isLive
          ? "No candidates running yet this round. They'll appear here as the optimizer scores them."
          : `Round ${round} carries no candidates.`}
      </Region>
    );
  }

  return (
    <section className="opt-detail-samples" aria-label="What this step scored">
      <div className="rsv-filters">
        <div className="rsv-cand-strip">
          <SegmentedControl<string>
            options={[
              { value: "all", label: `ALL (${candidates.length})` },
              ...candidates.map((c) => segmentFor(c, verdicts.get(c.reading.arm.label))),
            ]}
            value={candFilter}
            onChange={setCandFilter}
            ariaLabel="Which candidate's rows to show"
          />
        </div>
        {cells && <span className="rsv-count">{unitCount(totalRows, unit)}</span>}
      </div>
      <div className="rsv-groups">
        {groups.map((g) => {
          const { reading } = g.candidate;
          const { arm, outcome } = reading;
          const accuracy = reading.own?.accuracy?.value ?? null;
          const isCandSelected = isSelectedCandidate(
            selected,
            leafCycleId,
            arm.round,
            arm.candidate_id,
          );
          const { cached, cached_share: cachedShare } = reading.panel;
          const display = g.samples.slice(0, PANEL_RENDER_CAP);
          const truncated = g.samples.length - display.length;
          const verdict = verdicts.get(arm.label);
          return (
            <section
              key={g.candidate.key}
              className={`rsv-group${isCandSelected ? " selected" : ""}`}
            >
              <button
                type="button"
                className="rsv-group-head"
                onClick={() => onSelectCandidate(isCandSelected ? null : g.candidate)}
                title="Click to anchor lineage + fitness on this candidate"
              >
                <span className="rsv-cand-label">{arm.label}</span>
                <span className="rsv-tally">
                  {cells ? (
                    <span className="tag-cached" title="Each cell is an inner campaign">
                      {unitCount(g.samples.length, unit)}
                    </span>
                  ) : outcome === "invalid" ? (
                    <Badge
                      tone="danger"
                      title="Rejected by validation — it never ran, so the scores served beside it are synthetic."
                    >
                      rejected
                    </Badge>
                  ) : (
                    <span className="rsv-tally-score">
                      {[
                        reading.panel.scored != null ? `${reading.panel.scored} scored` : null,
                        accuracy != null ? fmtPct0(accuracy) : null,
                      ]
                        .filter((part) => part !== null)
                        .join(" · ")}
                    </span>
                  )}
                  {outcome && ARM_OUTCOMES_ENDED_EARLY.includes(outcome) && (
                    <Badge tone={outcome === "broken" ? "danger" : "default"}>
                      <Term content={TERMS[`arm_${outcome}`]}>
                        {outcome.replace("_", " ")}
                      </Term>
                    </Badge>
                  )}
                  {cached != null && cached > 0 && (
                    <span
                      className="tag-cached"
                      title={`Reused ${unitPlural(unit)} from a prior identical searchpoint — no fresh backend call`}
                    >
                      📖 {cachedShare === 1 ? "all cached" : `${cached} cached`}
                    </span>
                  )}
                </span>
              </button>
              {verdict && (verdict.changes !== "" || verdict.failures.length > 0) && (
                <div className="rsv-why">
                  {verdict.changes !== "" && (
                    <p className="rsv-changes" title={verdict.changes}>
                      {verdict.changes}
                    </p>
                  )}
                  {verdict.failures.map((f, i) => (
                    <p
                      key={`${f.reason}-${i}`}
                      className="rsv-reject-reason"
                      title={
                        f.allowed.length > 0
                          ? `${f.axis} — wanted ${f.allowed.join(", ")}`
                          : f.axis
                      }
                    >
                      {f.value}
                    </p>
                  ))}
                </div>
              )}
              {oneCandidate && g.spec && (
                <div className="rsv-spec-row">
                  <details className="rsv-spec">
                    <summary>What {arm.label} ran</summary>
                    <NodeSurface
                      node={null}
                      point={g.spec}
                      overlay={g.spec.pipeline_overlay}
                      schema={cv.schema}
                      mode="values"
                      compact
                    />
                  </details>
                  <CopyButton
                    data={{
                      label: arm.label,
                      resolved_pipeline_params: g.spec.pipeline_overlay,
                      prompt_fields: g.spec.origin_prompt_fields,
                    }}
                    title={`Copy what ${arm.label} ran`}
                  />
                </div>
              )}
              {!cells ? null : g.samples.length === 0 ? (
                <div className="rsv-empty-row">
                  {outcome === "invalid"
                    ? `No ${unitPlural(unit)} — it was rejected before it ran.`
                    : `No matching ${unitPlural(unit)}.`}
                </div>
              ) : (
                <div className="rsv-rows">
                  {display.map((s) => (
                    <PanelCellRow
                      key={s.key}
                      cell={s.query}
                      run={cells.get(panelCellKey(arm.label, s.query)) ?? null}
                      cached={s.cached}
                      onOpen={openRun}
                    />
                  ))}
                  {truncated > 0 && (
                    <div className="rsv-empty-row">
                      +{truncated} more (rendering capped at {PANEL_RENDER_CAP}).
                    </div>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>
      {!cells && (
        <MeasurementsPane
          preset={{
            round,
            scope: "cycle",
            candidateId: narrowTo,
            groupBy: "candidate",
          }}
        />
      )}
    </section>
  );
}

const PANEL_RENDER_CAP = 250;

function Region({ children }: { children: ReactNode }) {
  return (
    <section className="opt-detail-samples" aria-label="What this step scored">
      <div className="samples-empty">{children}</div>
    </section>
  );
}

// `rejected`, never `0%`: an invalid arm's served accuracy is `INVALID_SCORES`' synthetic 0.0.
function segmentFor(c: ArmRow, verdict: CandidateVerdict | undefined): Segment<string> {
  const { arm, outcome, own } = c.reading;
  if (outcome === "invalid") {
    const reason = verdict?.failures[0]?.value;
    return {
      value: arm.candidate_id,
      label: (
        <>
          {arm.label} · <span className="rsv-rejected">rejected</span>
        </>
      ),
      ariaLabel: `${arm.label}, rejected`,
      title: reason ?? "Rejected by validation before it ran.",
    };
  }
  const accuracy = own?.accuracy?.value;
  return {
    value: arm.candidate_id,
    label: accuracy != null ? `${arm.label} · ${fmtPct0(accuracy)}` : arm.label,
  };
}

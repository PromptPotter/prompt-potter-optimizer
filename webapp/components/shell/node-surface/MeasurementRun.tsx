"use client";
import { useMemo, useState, type ReactNode } from "react";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useRoundRows } from "@/lib/hooks/useRoundRows";
import { useViewedLineage } from "@/lib/lineage";
import {
  candidateSearchPoint,
  candidateVerdicts,
  innerPanelIndex,
  liveCandidateSearchPoint,
  panelCellKey,
  pathOf,
} from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import type { LineageNode } from "@/lib/api";
import {
  isSelectedCandidate,
  type CandidateRow,
  type ElectedRow,
  type NodeBlock,
  type SampleRow,
} from "@/lib/types";
import type { CandidateSearchPoint, CandidateVerdict } from "@/lib/derivations";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";
import { fmtPct0, unitCount, unitPlural } from "@/lib/format";
import { Badge, CopyButton, SegmentedControl, type Segment } from "@/components/ui";
import { NodeSurface } from "./NodeSurface";
import { PanelCellRow } from "./PanelCellRow";

// What a measurement node did this round: which candidates ran, why a rejected one has no rows,
// and what the named one ran. Scored rows are a `MeasurementsPane` preset; L4 cells list here.

export function MeasurementRun({
  block,
  round,
}: {
  // Resolved by `useRoundNodes`, the single live-vs-audit-twin resolver.
  block: NodeBlock | null;
  // Threaded from the panel, never re-resolved: a second `useEffectiveRound` can disagree for a tick.
  round: number;
}) {
  const { dash, status } = useDashboard();
  // The TARGET pipeline's schema: the scoring node is the optimizer's, what it scored is not.
  const cv = useConnector();
  // `leafIsL4` is the DECLARED backend type, never "did the tree find inner runs": cells still
  // minting have none, and would tally every null `is_hit` as a miss.
  const { viewedPath, leafCycleId, leafIsL4: isL4, drillInto } = useWorkspace();
  const { tree } = useViewedLineage();
  // Null = not L4; empty = L4 with the sandbox not yet read, and the cells still list. Keyed on
  // `course_label`, which the rows speak — a fork's bars carry the renumbered timeline label.
  const cells = useMemo(
    () => (isL4 ? innerPanelIndex(tree, viewedPath) : null),
    [isL4, tree, viewedPath],
  );
  const openRun = (run: LineageNode): void => {
    const at = pathOf(run).at(-1);
    if (at) drillInto(at.campaignId, at.cycleId);
  };
  const { setSelectionForCandidate, candidate: selected } = useSelection();
  const onSelectCandidate = (c: CandidateRow | null): void =>
    setSelectionForCandidate(
      c && leafCycleId
        ? {
            cycle_id: leafCycleId,
            round: c.round,
            candidate_id: c.candidate_id,
            label: c.label,
            accuracy: c.accuracy,
            is_winner: c.is_winner,
          }
        : null,
    );
  const {
    live: isLiveView,
    doc: roundDoc,
    loading: roundLoading,
    failure: roundFailure,
    rows: candidates,
    samples: samplesFor,
  } = useRoundRows(round);

  const [candFilter, setCandFilter] = useState<string>("all");

  // The rejection FLAG rides the row (no fetch); the REASON rides this block, lazily on a
  // historical round — so a row never shows a rejection as a percentage while waiting.
  const verdicts = useMemo(() => candidateVerdicts(block), [block]);

  const groups = useMemo(() => {
    const out: {
      candidate: ElectedRow;
      samples: SampleRow[];
      spec: CandidateSearchPoint | null;
    }[] = [];
    for (const c of candidates) {
      const filtered = samplesFor(c);
      // Off the same source its rows came from — never a merge.
      const spec =
        c.source === "inflight"
          ? liveCandidateSearchPoint(dash, c.label)
          : candidateSearchPoint(roundDoc, c.candidate_id);
      out.push({ candidate: c, samples: filtered, spec });
    }
    if (candFilter !== "all") {
      return out.filter((g) => g.candidate.candidate_id === candFilter);
    }
    return out;
  }, [candidates, candFilter, dash, roundDoc, samplesFor]);

  const totalRows = useMemo(
    () => groups.reduce((n, g) => n + g.samples.length, 0),
    [groups],
  );

  // Served, never derived from `cells`: that says what a row renders as, this what it is called.
  const unit = dash?.measured_unit ?? "sample";
  const oneCandidate = candFilter !== "all";
  // An in-flight `candidate_id` is minted client-side (`liveCandidateId`) and names nothing on the
  // server, so the live round stays unnarrowed.
  const picked = candidates.find((c) => c.candidate_id === candFilter);
  const narrowTo = picked && picked.source !== "inflight" ? picked.candidate_id : undefined;

  if (!isLiveView && roundLoading) {
    return <Region>Loading round {round}…</Region>;
  }
  if (!isLiveView && roundFailure) {
    return <Region>Could not load round {round}.</Region>;
  }
  if (candidates.length === 0) {
    return (
      <Region>
        {isLiveView && status === "live"
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
              ...candidates.map((c) => segmentFor(c, verdicts.get(c.label))),
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
          const isCandSelected = isSelectedCandidate(
            selected,
            leafCycleId,
            g.candidate.round,
            g.candidate.candidate_id,
          );
          const cached = g.samples.reduce((n, s) => n + (s.cached ? 1 : 0), 0);
          const display = g.samples.slice(0, PANEL_RENDER_CAP);
          const truncated = g.samples.length - display.length;
          const verdict = verdicts.get(g.candidate.label);
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
                <span className="rsv-cand-label">{g.candidate.label}</span>
                <span className="rsv-tally">
                  {cells ? (
                    <span className="tag-cached" title="Each cell is an inner campaign">
                      {unitCount(g.samples.length, unit)}
                    </span>
                  ) : g.candidate.invalid ? (
                    /* Never a rate: the 0.0 served beside it is `INVALID_SCORES`' synthetic score. */
                    <Badge
                      tone="danger"
                      title="Rejected by validation — it never ran, so the scores served beside it are synthetic."
                    >
                      rejected
                    </Badge>
                  ) : (
                    <span className="rsv-tally-score">
                      {g.candidate.n_samples ?? g.samples.length} scored
                      {g.candidate.accuracy != null && ` · ${fmtPct0(g.candidate.accuracy)}`}
                    </span>
                  )}
                  {cached > 0 && (
                    <span
                      className="tag-cached"
                      title={`Reused ${unitPlural(unit)} from a prior identical searchpoint — no fresh backend call`}
                    >
                      📖 {cached === g.samples.length ? "all cached" : `${cached} cached`}
                    </span>
                  )}
                </span>
              </button>
              {/* Both sentences are the producer's own; nothing here composes copy about it. */}
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
                    <summary>What {g.candidate.label} ran</summary>
                    <NodeSurface
                      node={null}
                      point={g.spec}
                      overlay={g.spec.pipeline_overlay}
                      schema={cv.nodeConfigSchema}
                      schemaStatus={cv.pipelineStatus}
                      outputSchema={cv.nodeOutputSchema}
                      mode="values"
                      compact
                    />
                  </details>
                  {/* Beside the disclosure, never in its `<summary>`. */}
                  <CopyButton
                    data={{
                      label: g.candidate.label,
                      resolved_pipeline_params: g.spec.pipeline_overlay,
                      prompt_fields: g.spec.origin_prompt_fields,
                    }}
                    title={`Copy what ${g.candidate.label} ran`}
                  />
                </div>
              )}
              {!cells ? null : g.samples.length === 0 ? (
                <div className="rsv-empty-row">
                  {g.candidate.invalid
                    ? `No ${unitPlural(unit)} — it was rejected before it ran.`
                    : `No matching ${unitPlural(unit)}.`}
                </div>
              ) : (
                <div className="rsv-rows">
                  {display.map((s) => (
                    <PanelCellRow
                      key={s.key}
                      cell={s.query}
                      run={cells.get(panelCellKey(g.candidate.label, s.query)) ?? null}
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

// A rejected candidate reads `rejected`, never `0%`: its served accuracy is `INVALID_SCORES`' synthetic 0.0.
function segmentFor(c: ElectedRow, verdict: CandidateVerdict | undefined): Segment<string> {
  if (c.invalid) {
    const reason = verdict?.failures[0]?.value;
    return {
      value: c.candidate_id,
      label: (
        <>
          {c.label} · <span className="rsv-rejected">rejected</span>
        </>
      ),
      ariaLabel: `${c.label}, rejected`,
      title: reason ?? "Rejected by validation before it ran.",
    };
  }
  return {
    value: c.candidate_id,
    label: c.accuracy != null ? `${c.label} · ${fmtPct0(c.accuracy)}` : c.label,
  };
}

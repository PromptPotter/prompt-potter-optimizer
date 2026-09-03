"use client";
import { useMemo, useState, type ReactNode } from "react";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { useViewedLineage } from "@/lib/lineage";
import {
  candidateSearchPoint,
  candidateVerdicts,
  innerPanelIndex,
  liveCandidateSearchPoint,
  panelCellKey,
  pathOf,
  samplesForRow,
} from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import type { LineageNode } from "@/lib/api";
import {
  isSelectedCandidate,
  type CandidateRow,
  type ElectedRow,
  type NodeBlock,
  type SampleRow,
  type SampleStatus,
} from "@/lib/types";
import type { CandidateSearchPoint, CandidateVerdict } from "@/lib/derivations";
import { SampleRowItem, SAMPLE_RENDER_CAP } from "@/components/shell/samples/SampleRowItem";
import { fmtPct0, unitCount, unitPlural } from "@/lib/format";
import { Badge, SegmentedControl, type Segment } from "@/components/ui";
import { NodeSurface } from "./NodeSurface";
import { PanelCellRow } from "./PanelCellRow";

// What a MEASUREMENT node did this round — the run half of its panel, and the peer of
// `L1Variants` (which is the run half of `l1_generate`'s). A measurement node runs a whole
// pipeline rather than a prompt, so what it DID is a roster of candidates and the rows each one
// produced; it has no rendered input and no response, and a panel that offers those two columns
// is describing an LLM call this node never makes.
//
// It draws no card and no title: the panel header above already names the node and the round it
// is showing, and the round AXIS owns which round that is.
//
// Live mode reads `dashboard.json` only; historical mode reads `round_NNNN.json` only — the two
// paths never merge (`webapp/CLAUDE.md` no-stitch rule). The candidate list comes from the shared
// spine in both modes, so the groups stay aligned with lineage + fitness.

// Derived from `SampleStatus`, not re-spelled — a hand-written pair naming two marks of three
// leaves the third reachable only under ALL, which for ERR hides faults from a hunt for faults.
type StatusFilter = "all" | SampleStatus;

const STATUS_FILTERS: readonly Segment<StatusFilter>[] = [
  { value: "all", label: "ALL" },
  { value: "HIT", label: "HIT" },
  { value: "MISS", label: "MISS" },
  { value: "ERR", label: "ERR" },
];

export function MeasurementRun({
  block,
  round,
}: {
  // This node's own audit block, resolved by `useRoundNodes` in the panel above — the single
  // resolver that picks live vs audit twin. It carries the one thing no candidate row does: the
  // scoring node's account of WHY a rejected candidate has no rows.
  block: NodeBlock | null;
  // The round being shown, threaded from the panel rather than re-resolved. `useRoundNodes`
  // already went through `useEffectiveRound` to get the block; asking a second time would be two
  // reads of one answer that can disagree for a tick. Never null: the panel renders no run half
  // at all before a campaign has one, so a "no rounds yet" state here would be unreachable.
  round: number;
}) {
  const { dash, status } = useDashboard();
  // The TARGET pipeline's schema — what a candidate's config rows are typed against. The
  // scoring node belongs to the optimizer, but what it scored is a target searchpoint.
  const cv = useConnector();
  // `leafIsL4`: are this course's samples inner campaigns rather than scored rows? The leaf
  // campaign's DECLARED backend type — not "did the tree find inner runs". An L4 course whose
  // first cells are still minting has no runs filed yet, so inferring the mode from the tree
  // would render those cells as scored rows and tally every null `is_hit` as a MISS.
  const { viewedPath, leafCycleId, leafIsL4: isL4, drillInto } = useWorkspace();
  const { tree } = useViewedLineage();
  // PANEL MODE. Non-null when the samples are inner campaigns: `(candidate_label, cell)` → the run
  // that measured that cell. Null vs empty is a real distinction — empty means L4 with the sandbox
  // not yet read, and the cells still list.
  //
  // `innerPanelIndex` addresses into the tree by path rather than reading its top-level children,
  // and keys on `course_label` — the minting course's private position, which is what the rows
  // below (read from the leaf's own `dashboard.json`) speak. A fork's attempts are renumbered onto
  // the campaign timeline, which is why the label the bars carry and the label the rows carry are
  // two different strings.
  const cells = useMemo(
    () => (isL4 ? innerPanelIndex(tree, viewedPath) : null),
    [isL4, tree, viewedPath],
  );
  const openRun = (run: LineageNode): void => {
    const at = pathOf(run).at(-1);
    if (at) drillInto(at.campaignId, at.cycleId);
  };
  const { setSelectionForCandidate, candidate: selected } = useSelection();
  // The groups are read from the leaf's round source, so the leaf is the cycle
  // that produced these candidates — the selection names it, and the panes it
  // scopes read that same hop.
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
    isLive: isLiveView,
    doc: roundDoc,
    loading: roundLoading,
    error: roundError,
  } = useRoundSource(viewedPath, round, dash);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [candFilter, setCandFilter] = useState<string>("all");

  // Candidate list for this round — single source of truth shared with
  // the candidates card via the spine hook. Round 0 is the origin (one
  // candidate, "C0") and shows its per-sample stream from round_0000.json like
  // any round.
  const { byRound } = useRoundCandidates();
  const candidates: ElectedRow[] = useMemo(
    () => byRound.get(round) ?? [],
    [byRound, round],
  );

  // Why a candidate produced what it produced. The FLAG that a candidate was rejected rides the
  // candidate row (no fetch, so the correctness fix lands instantly); the REASON rides this block,
  // which on a historical round is a lazy fetch. That asymmetry is deliberate — a row never
  // renders a rejection as a percentage while waiting, it just cannot yet say why.
  const verdicts = useMemo(() => candidateVerdicts(block), [block]);

  // Build per-candidate samples lists. Live mode pulls the served rows off the in-flight
  // projection; historical mode pulls from the round file's `all_candidate_results`. Both
  // readers return the same `SampleRow` shape so the renderer below stays source-agnostic.
  const groups = useMemo(() => {
    const out: {
      candidate: ElectedRow;
      samples: SampleRow[];
      spec: CandidateSearchPoint | null;
    }[] = [];
    for (const c of candidates) {
      // `samplesForRow` selects live vs historical off the row's own `source`
      // tag (the spine sets it) — same routing the candidates card's bars use, never a
      // merge. `roundDoc` is null on the live round (the fetch is idled), and
      // an in-flight row reads `dash`, so the source is unambiguous.
      const raw = samplesForRow(c, dash, roundDoc);
      // An L4 cell has no mark to filter on — it was optimized, not scored, so every
      // `status` is null. The control is hidden in that mode; skipping the filter here
      // keeps a stale `HIT` pick from blanking the panel.
      const filtered = isL4
        ? raw
        : raw.filter((s) => statusFilter === "all" || s.status === statusFilter);
      // WHAT this candidate ran, off the same source its rows came from — the scoring node
      // fires once per candidate, so a panel that lists the rows and not the specification
      // shows the outcome of a program it never names. Same live/historical switch as
      // `samplesForRow`: never a merge.
      const spec =
        c.source === "inflight"
          ? liveCandidateSearchPoint(dash, c.candidate_id)
          : candidateSearchPoint(roundDoc, c.candidate_id);
      out.push({ candidate: c, samples: filtered, spec });
    }
    if (candFilter !== "all") {
      return out.filter((g) => g.candidate.candidate_id === candFilter);
    }
    return out;
  }, [candidates, candFilter, statusFilter, dash, roundDoc, isL4]);

  const totalRows = useMemo(
    () => groups.reduce((n, g) => n + g.samples.length, 0),
    [groups],
  );

  // SERVED (`dashboard.json::measured_unit`). Separate from `cells` on purpose: that flag says
  // what a row RENDERS AS, this says what it is CALLED, and deriving one from the other makes
  // the noun a property of client view state.
  const unit = dash?.measured_unit ?? "sample";
  // The spec shows only once the toggle names ONE candidate. Under ALL the panel is the
  // round's whole scroll — which is what it is for — and N prompts stacked inside it would
  // bury the rows the operator opened it to read.
  const oneCandidate = candFilter !== "all";

  if (!isLiveView && roundLoading) {
    return <Region>Loading round {round}…</Region>;
  }
  if (!isLiveView && roundError) {
    return (
      <Region>
        Could not load round {round}: {roundError}
      </Region>
    );
  }
  if (candidates.length === 0) {
    // A live round still waiting on its first candidate is not a completed round that
    // carried none — two absences, said differently.
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
        {/* A TOGGLE, not a dropdown: the scoring node runs once per candidate, so which one
            you are reading is the panel's primary axis and a menu hid it behind a click.
            Scrolls sideways past a handful rather than shrinking — `webapp/CLAUDE.md`
            § Stylesheet organization: wide content scrolls in its own container. */}
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
        {/* Hidden in panel mode: an L4 cell has no HIT/MISS to filter on. */}
        {!cells && (
          <SegmentedControl
            options={STATUS_FILTERS}
            value={statusFilter}
            onChange={setStatusFilter}
            ariaLabel="Sample status filter"
          />
        )}
        <span className="rsv-count">{unitCount(totalRows, unit)}</span>
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
          const display = g.samples.slice(0, SAMPLE_RENDER_CAP);
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
                    /* Rejected before it cost a sample. Never a count and never a rate: the
                       0.0 served beside it is `INVALID_SCORES`' synthetic score, and a
                       denominator of zero has no percentage to report. */
                    <Badge
                      tone="danger"
                      title="Rejected by validation — it never ran, so the scores served beside it are synthetic."
                    >
                      rejected
                    </Badge>
                  ) : (
                    /* Both numbers are SERVED and share ONE denominator: the
                       candidate's scored-sample count and the accuracy over it.
                       `g.samples.length` is the count AFTER the HIT/MISS filter,
                       so pairing it with an unfiltered rate would caption the
                       rate with someone else's denominator; the filter's effect
                       stays legible in the rows and in the roster count above. */
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
              {/* What this candidate TRIED, and — where it never ran — what validation said
                  about it. Both sentences are the producer's own; nothing here composes copy
                  about a decision it did not make. */}
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
              {/* WHAT this candidate ran. The one node surface every spec reads through, so a
                  prompt shown here and the same prompt on the hero cannot drift. Folded: the
                  rows are the subject, the program is the thing you check against them. */}
              {oneCandidate && g.spec && (
                <details className="rsv-spec">
                  <summary>What {g.candidate.label} ran</summary>
                  <NodeSurface
                    node={null}
                    point={g.spec}
                    configSeed={g.spec.pipeline_overlay}
                    schema={cv.nodeConfigSchema}
                    outputSchema={cv.nodeOutputSchema}
                    mode="values"
                    compact
                  />
                </details>
              )}
              {g.samples.length === 0 ? (
                <div className="rsv-empty-row">
                  {g.candidate.invalid
                    ? `No ${unitPlural(unit)} — it was rejected before it ran.`
                    : `No matching ${unitPlural(unit)}.`}
                </div>
              ) : (
                <div className="rsv-rows">
                  {display.map((s) =>
                    cells ? (
                      <PanelCellRow
                        key={s.key}
                        cell={s.query}
                        run={cells.get(panelCellKey(g.candidate.label, s.query)) ?? null}
                        cached={s.cached}
                        onOpen={openRun}
                      />
                    ) : (
                      <SampleRowItem key={s.key} row={s} />
                    ),
                  )}
                  {truncated > 0 && (
                    <div className="rsv-empty-row">
                      +{truncated} more (rendering capped at {SAMPLE_RENDER_CAP}).
                    </div>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </section>
  );
}

// Every absence wears the same frame the roster does, so the panel does not resize under a
// reader who switched rounds.
function Region({ children }: { children: ReactNode }) {
  return (
    <section className="opt-detail-samples" aria-label="What this step scored">
      <div className="samples-empty">{children}</div>
    </section>
  );
}

// One candidate's chip in the strip. A rejected candidate reads `rejected`, never a percentage:
// the served `accuracy` beside it is `INVALID_SCORES`' synthetic 0.0, and rendering that as `0%`
// spells "measured, got everything wrong" for a candidate that was never measured at all.
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

"use client";
import { memo, useMemo } from "react";
import { fmtPct0 } from "@/lib/format";
import {
  fmtHeadlineValue,
  headlineMetricLabel,
  type HeadlineMetric,
} from "@/lib/derivations";
import { cx } from "@/lib/cx";
import { shortFamilyTail } from "@/lib/ids";
import { useSelection } from "@/lib/SelectionContext";
import { isSelectedCandidate } from "@/lib/types";
import { heartsText } from "@/lib/derivations";
import type { LineageNode } from "@/lib/api";
import {
  CAND_STUB,
  COL_W,
  HEADER_H,
  KIND_GLYPH,
  TRIGGER_GLYPH,
  LANE_H,
  LEFT_PAD,
  NODE_R,
  RIGHT_PAD,
  TOP_PAD,
  layout,
  placeNodes,
  type LaneLayout,
  type RoundNodePos,
} from "./forest-layout";

// A course's operator-facing name: a root wears its dataset, a branch its short tail.
function courseName(course: LineageNode): string {
  return course.course_kind === "root"
    ? course.dataset_name || course.id
    : shortFamilyTail(course.id);
}

// One expanded candidate node: parent→child slant is drawn as a seg upstream;
// this renders the lineage-style stub + label, clickable for candidate
// selection. Memo'd so unrelated lane toggles don't re-render every node.
const CandidateNode = memo(function CandidateNode({
  n,
  accuracy,
  theta,
  metric,
  selected,
  onPick,
  dimmed,
  alt,
  divergence,
}: {
  n: RoundNodePos;
  // The live percent-metric value painted on this node (the `valueByKey` overlay)
  // — separate from geometry so a value tick re-renders only this text, not the
  // memoized layout. Used as the node value for the accuracy/composite metrics.
  accuracy: number | null;
  // Difficulty-adjusted ability — the value painted when `metric === "ability"`,
  // and always shown in the tooltip (the metric the winner is elected on).
  theta: number | null;
  // Which fitness number the operator selected for the node value.
  metric: HeadlineMetric;
  selected: boolean;
  onPick: (n: RoundNodePos) => void;
  // Mask overlay (served): `dimmed` = in the counterfactual subtree past a
  // divergence; `alt` = this candidate is the one the lens would have elected;
  // `divergence` = this is the recorded winner AT the divergence round (the last
  // agreed-upon point — glows red even when the lens names no alternative).
  dimmed: boolean;
  alt: boolean;
  divergence: boolean;
}) {
  return (
    <g
      className={cx(
        "lineage-node",
        selected && "selected",
        dimmed && "mask-divergent",
        alt && "mask-alt",
        divergence && "mask-divergence",
      )}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`Round ${n.round} candidate ${n.candidateLabel}, ${headlineMetricLabel(metric)} ${fmtHeadlineValue(metric, accuracy, theta)}${n.isWinner ? ", round winner" : ""}${divergence ? ", divergence point under the lens" : ""}${alt ? ", would be elected under the scoring lens" : ""}${dimmed ? ", counterfactual under the scoring lens" : ""}`}
      onClick={() => onPick(n)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onPick(n);
        }
      }}
      style={{ cursor: "pointer" }}
    >
      <title>
        {n.candidateLabel} · {fmtHeadlineValue(metric, accuracy, theta)}
        {metric !== "ability" && typeof theta === "number"
          ? ` · ability θ ${theta.toFixed(2)}`
          : ""}
        {n.isWinner
          ? "\nround winner — elected on difficulty-adjusted ability θ, not raw accuracy"
          : ""}
      </title>
      {/* The alternative candidate is marked by its own branch line glowing red
          (`.mask-alt .lineage-stub`) — no glyph. */}
      <line
        x1={n.x - CAND_STUB}
        y1={n.y}
        x2={n.x}
        y2={n.y}
        className={cx("lineage-stub", n.isWinner && "winner")}
      />
      <text
        x={n.x + 4}
        y={n.y + 3}
        className={cx("lineage-label", n.isWinner && "winner", selected && "selected")}
      >
        {n.candidateLabel} {fmtHeadlineValue(metric, accuracy, theta)}
      </text>
      {/* Invisible click target: the candidate's own slot — its stub plus the one
          column-width its label occupies before the next round's node. */}
      <rect x={n.x - CAND_STUB} y={n.y - 10} width={CAND_STUB + COL_W} height={20} fill="transparent" />
    </g>
  );
});

// The campaign's cladogram — the ONE served tree, rendered. Its own <svg> so lane
// math is never reconciled across surfaces.
export function Forest({
  tree,
  campaignId,
  cycleId,
  valueByKey,
  thetaByKey,
  metric,
  expanded,
  onLaneActivate,
  onSelectCycle,
}: {
  // The served genealogy's root course. Nodes alternate course → candidate →
  // (course | sample), so forks and L4 inner runs need no special case here.
  tree: LineageNode;
  campaignId: string;
  cycleId: string | null;
  // Live per-candidate percent metric (accuracy/composite), keyed
  // `{cycleId}::{candidateId}` — painted onto nodes outside the geometry memo so a
  // value tick costs only a text re-render.
  valueByKey: ReadonlyMap<string, number | null>;
  // Same-key overlay of difficulty-adjusted ability θ — the node value when
  // `metric === "ability"`, and always shown in node tooltips so a θ-elected winner
  // below a higher-accuracy sibling is explainable in place.
  thetaByKey: ReadonlyMap<string, number | null>;
  // Operator-selected headline metric for the node values (accuracy/composite/θ).
  metric: HeadlineMetric;
  expanded: ReadonlySet<string>;
  // A lane clicked away from a searchpoint node — toggles that cycle's lane
  // between its expanded candidate cladogram and the compact summary row, in
  // place. Never changes the dashboard's selected cycle.
  onLaneActivate: (cycleId: string) => void;
  // Navigate the dashboard to a cycle — fired when a candidate in a non-selected
  // lane is clicked (the inspector/samples follow the searchpoint).
  onSelectCycle: (campaignId: string, cycleId: string) => void;
}) {
  const { candidate, setSelectionForCandidate } = useSelection();
  // The live fitness painted on a node — the `valueByKey` overlay, looked up by
  // the same candidate identity the bars use. Outside the layout memo, so it
  // updates each poll without re-flowing the tree.
  const valOf = (n: RoundNodePos): number | null =>
    valueByKey.get(`${n.cycleId}::${n.candidateId}`) ?? null;
  // Difficulty-adjusted ability for the node tooltip — what the winner was elected on.
  const thetaOf = (n: RoundNodePos): number | null =>
    thetaByKey.get(`${n.cycleId}::${n.candidateId}`) ?? null;
  // Layout is pure and the tree changes identity only on a real refetch, so this
  // memo re-runs only on a shape change (new round / candidate / winner flip / lane
  // toggle), never on a bare 2 s poll — the value overlay rides outside it.
  const { laneByCycle, totalLaneRows, maxCol } = useMemo(
    () => layout(tree, expanded),
    [tree, expanded],
  );
  const { nodes, segs } = useMemo(() => placeNodes(laneByCycle), [laneByCycle]);
  // The candidates a lens would have elected instead. The marker rides the round's
  // WINNER and names its alternative, so the alternative learns of itself here —
  // one pass over the placed nodes, no parallel array to re-join.
  const altIds = useMemo(
    () =>
      new Set(
        nodes
          .map((n) => n.divergence?.alternative_candidate_id)
          .filter((id): id is string => !!id),
      ),
    [nodes],
  );
  const height = TOP_PAD + totalLaneRows * LANE_H + 8;
  const width = LEFT_PAD + (maxCol + 1) * COL_W + RIGHT_PAD;

  // Round-number header — one label per column across the whole family.
  const headerCols: number[] = [];
  for (let c = 1; LEFT_PAD + c * COL_W <= width - RIGHT_PAD + COL_W / 2; c += 1) {
    headerCols.push(c);
  }

  const laneList = [...laneByCycle.values()];
  // Band y/height for a lane (covers all its rows when expanded).
  const bandTop = (l: LaneLayout): number => TOP_PAD + l.laneOffset * LANE_H - LANE_H / 2 + 2;
  const bandH = (l: LaneLayout): number => l.laneSpan * LANE_H - 4;

  // Candidate click: inspect that searchpoint. A node in a non-selected lane also
  // navigates the dashboard to its cycle, so the inspector/samples follow it — but
  // it SELECTS either way: the selection names `n.cycleId`, which is the cycle
  // being navigated to, so the provider's cycle-change clear keeps it. This used to
  // navigate and drop the pick on the floor, because the clear ate any candidate
  // written across a cycle change.
  const onPickCandidate = (n: RoundNodePos): void => {
    if (n.cycleId !== cycleId) onSelectCycle(campaignId, n.cycleId);
    const isSel = isSelectedCandidate(candidate, n.cycleId, n.round, n.candidateId);
    setSelectionForCandidate(
      isSel
        ? null
        : {
            cycle_id: n.cycleId,
            round: n.round,
            candidate_id: n.candidateId,
            label: n.candidateLabel,
            accuracy: valOf(n),
            is_winner: n.isWinner,
          },
    );
  };

  return (
    <div className="family-cladogram-forest">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          xmlns="http://www.w3.org/2000/svg"
          className="family-cladogram-svg"
          aria-label="Session lineage cladogram"
          shapeRendering="crispEdges"
        >
          {headerCols.map((c) => {
            const x = LEFT_PAD + c * COL_W;
            return (
              <g key={`hdr-${c}`} className="family-cladogram-header-col">
                <line
                  x1={x}
                  y1={HEADER_H}
                  x2={x}
                  y2={height - 4}
                  className="family-cladogram-gridline"
                />
                <text
                  x={x}
                  y={HEADER_H - 4}
                  className="family-cladogram-headerlabel"
                  textAnchor="middle"
                >
                  R{c}
                </text>
              </g>
            );
          })}

          {segs.map((s, i) => (
            <line
              key={`seg-${i}`}
              x1={s.x1}
              y1={s.y1}
              x2={s.x2}
              y2={s.y2}
              className={cx("family-cladogram-branch", s.variant === "fork" && "fork")}
            />
          ))}

          {/* Selected-lane highlight — covers the whole band when expanded. */}
          {laneList.map((l) => {
            if (l.course.id !== cycleId) return null;
            return (
              <rect
                key={`hl-${l.course.id}`}
                x={0}
                y={bandTop(l)}
                width={width}
                height={bandH(l)}
                className="family-cladogram-lane-selected"
              />
            );
          })}

          {/* Per-lane activation overlay — painted before nodes so their clicks
              win; clicks on the row background fall through to here. A different
              fork selects+expands it; the selected fork toggles expanded ↔
              compact. This is the row-background collapse target (a searchpoint
              node always wins over it). */}
          {laneList.map((l) => {
            const course = l.course;
            const isEmpty = l.candidates.length === 0;
            const roundCount = new Set(l.candidates.map((c) => c.round)).size;
            const verb = l.expanded ? "Collapse" : "Expand";
            return (
              <rect
                key={`lanehit-${course.id}`}
                x={0}
                y={bandTop(l)}
                width={width}
                height={bandH(l)}
                className="family-cladogram-lane-hit"
                role="button"
                tabIndex={0}
                aria-label={`${verb} ${courseName(course)}`}
                onClick={() => onLaneActivate(course.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onLaneActivate(course.id);
                  }
                }}
                style={{ cursor: "pointer" }}
              >
                <title>
                  {course.id}
                  {`\n${course.course_kind}`}
                  {course.task ? ` · ${course.task}` : ""}
                  {course.trigger === "operator_steered"
                    ? ` · steered${course.steered_by ? ` by ${course.steered_by}` : ""}`
                    : ""}
                  {course.state ? ` · ${course.state}` : ""}
                  {course.best_accuracy != null ? ` · best ${fmtPct0(course.best_accuracy)}` : ""}
                  {isEmpty
                    ? "\nNo post-divergence rounds — use Clean up in the header to prune"
                    : `\n${roundCount} round(s) · click row to ${l.expanded ? "collapse" : "expand"}`}
                </title>
              </rect>
            );
          })}

          {/* Collapsed summary nodes (circles). */}
          {nodes
            .filter((n) => !n.isExpanded)
            .map((n) => {
              const cycleSelected = n.cycleId === cycleId;
              const layoutEntry = laneByCycle.get(n.cycleId);
              const cycName = layoutEntry ? courseName(layoutEntry.course) : n.cycleId;
              const rowLabelText = n.isLastInLane && layoutEntry ? cycName : null;
              // The lane's ♥ bank, as glyphs — the cladogram is an <svg>, so the shared
              // <Hearts> component can't mount here; `heartsText` is the same derivation
              // rendered as text. Empty string when the cycle isn't in lives mode.
              const laneHearts = layoutEntry
                ? heartsText(layoutEntry.course.hearts, layoutEntry.course.lives_cap)
                : "";
              const isDivergence = n.divergence !== null;
              const isDivergent = n.divergent;
              return (
                <g
                  key={`n-${n.cycleId}-${n.round}`}
                  className={cx(
                    "family-cladogram-node",
                    cycleSelected && "selected",
                    isDivergent && "mask-divergent",
                    isDivergence && "mask-divergence",
                  )}
                  role="button"
                  tabIndex={0}
                  aria-label={`Expand ${cycName}`}
                  onClick={() => onLaneActivate(n.cycleId)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onLaneActivate(n.cycleId);
                    }
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={NODE_R}
                    className={`family-cladogram-dot kind-${n.courseKind}`}
                  />
                  {isDivergence && (
                    <circle
                      cx={n.x}
                      cy={n.y}
                      r={NODE_R + 3}
                      className="family-cladogram-divergence-ring"
                    />
                  )}
                  <text
                    x={n.x}
                    y={n.y - 6}
                    className="family-cladogram-roundlabel"
                    textAnchor="middle"
                  >
                    R{n.round} {fmtHeadlineValue(metric, valOf(n), thetaOf(n))}
                  </text>
                  {rowLabelText && (
                    <text x={n.x + 8} y={n.y + 3} className="family-cladogram-cyclelabel">
                      <tspan className="family-cladogram-glyph">
                        {KIND_GLYPH[n.courseKind]}
                        {TRIGGER_GLYPH[n.trigger] ?? ""}
                      </tspan>
                      <tspan dx="4">{rowLabelText}</tspan>
                      {laneHearts && (
                        <tspan dx="6" className="family-cladogram-hearts">
                          {laneHearts}
                        </tspan>
                      )}
                    </text>
                  )}
                  <title>
                    {n.cycleId} · R{n.round} · {fmtHeadlineValue(metric, valOf(n), thetaOf(n))}
                    {metric !== "ability" && typeof thetaOf(n) === "number"
                      ? ` · ability θ ${thetaOf(n)!.toFixed(2)}`
                      : ""}
                    {n.candidateLabel ? `\n${n.candidateLabel}` : ""}
                    {isDivergence ? "\ndivergence under the scoring lens" : ""}
                    {isDivergent ? "\ncounterfactual under the scoring lens" : ""}
                  </title>
                </g>
              );
            })}

          {/* Expanded candidate nodes (lineage-style stubs). */}
          {nodes
            .filter((n) => n.isExpanded)
            .map((n) => (
              <CandidateNode
                key={`c-${n.cycleId}-${n.round}-${n.candidateId}`}
                n={n}
                accuracy={valOf(n)}
                theta={thetaOf(n)}
                metric={metric}
                selected={isSelectedCandidate(candidate, n.cycleId, n.round, n.candidateId)}
                onPick={onPickCandidate}
                dimmed={n.divergent}
                alt={altIds.has(n.candidateId)}
                divergence={n.divergence !== null}
              />
            ))}

          {/* Expanded lanes carry their cycle label beside the last winner. */}
          {nodes
            .filter((n) => n.isExpanded && n.isLastInLane)
            .map((n) => {
              const course = laneByCycle.get(n.cycleId)?.course;
              if (!course) return null;
              return (
                <text
                  key={`elabel-${n.cycleId}`}
                  x={n.x + CAND_STUB + 84}
                  y={n.y + 3}
                  className="family-cladogram-cyclelabel"
                >
                  <tspan className="family-cladogram-glyph">
                    {KIND_GLYPH[course.course_kind ?? "root"]}
                    {TRIGGER_GLYPH[course.trigger] ?? ""}
                  </tspan>
                  <tspan dx="4">{courseName(course)}</tspan>
                </text>
              );
            })}

        </svg>
    </div>
  );
}

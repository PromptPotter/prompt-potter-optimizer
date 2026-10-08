"use client";
import { memo, useCallback, useMemo } from "react";
import { fmtPct0, fmtTheta } from "@/lib/format";
import {
  fmtDisplayValue,
  displayMetricLabel,
  nodeKeyOf,
  nodeMetric,
  type DisplayMetric,
} from "@/lib/derivations";
import { cx } from "@/lib/cx";
import { pressable } from "@/components/ui";
import { pathLeaf, shortFamilyTail } from "@/lib/ids";
import { heartsText } from "@/lib/derivations";
import type { LineageNode } from "@/lib/api";
import { STOP_REASON_LABELS } from "@/lib/api/types.generated";
import {
  DIRECTION_GLYPH,
  HEADER_H,
  KIND_GLYPH,
  TRIGGER_GLYPH,
  LANE_H,
  NODE_R,
  TOP_PAD,
  extentKeys,
  layout,
  placeNodes,
  type CladogramAnchor,
  type Density,
  type LaneLayout,
  type RoundNodePos,
} from "./forest-layout";

export interface CladogramChannel extends CladogramAnchor {
  // A `var()` reference (`theme.ts::seriesVar`), never a resolved value.
  ink: string;
}

export interface CladogramCtx {
  viewedKey: string | null;
  isPicked: (n: RoundNodePos) => boolean;
  onPickCandidate: (n: RoundNodePos) => void;
  channels: readonly CladogramChannel[];
  // The family is cut to this channel's extent; `null` draws the whole family.
  clip: CladogramAnchor | null;
  // Candidate ids (the space `parent_ids` speaks) whose config was edited, plus descendants.
  invalidated?: ReadonlySet<string>;
}

function courseName(course: LineageNode): string {
  return course.course_kind === "root"
    ? course.dataset_name || course.id
    : shortFamilyTail(course.id);
}

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
  invalidated,
  ink,
  d,
}: {
  n: RoundNodePos;
  accuracy: number | null;
  theta: number | null;
  metric: DisplayMetric;
  selected: boolean;
  onPick: (n: RoundNodePos) => void;
  dimmed: boolean;
  alt: boolean;
  divergence: boolean;
  invalidated: boolean;
  ink: string | null;
  d: Density;
}) {
  const retiredBy = n.retiredBy;
  const stampsTheta = n.node.stamps_theta;
  const shown = nodeMetric(metric, stampsTheta);
  return (
    <g
      className={cx(
        "lineage-node",
        selected && "selected",
        dimmed && "mask-divergent",
        retiredBy && "retired",
        alt && "mask-alt",
        divergence && "mask-divergence",
        invalidated && "unknown",
        ink && "channel",
      )}
      {...pressable(() => onPick(n))}
      aria-pressed={selected}
      aria-label={`Round ${n.round} candidate ${n.candidateLabel}, ${invalidated ? "unknown — a setting was changed at or above this point" : `${displayMetricLabel(shown)} ${fmtDisplayValue(shown, accuracy, theta)}`}${n.crown === "elected" ? ", round winner" : ""}${ink ? ", a channel of the comparison" : ""}${retiredBy ? ", retired — the run branched away and continued elsewhere" : ""}${divergence ? ", divergence point under the lens" : ""}${alt ? ", would be elected under the scoring lens" : ""}${dimmed ? ", counterfactual under the scoring lens" : ""}`}
      style={{ cursor: "pointer" }}
    >
      <title>
        {n.candidateLabel} ·{" "}
        {invalidated
          ? "unknown"
          : fmtDisplayValue(shown, accuracy, theta)}
        {!invalidated && shown !== "ability" && typeof theta === "number"
          ? ` · ability θ ${fmtTheta(theta)}`
          : ""}
        {invalidated
          ? "\na setting was changed here or above — nothing ran at that value, so this point's numbers describe a searchpoint it no longer is"
          : ""}
        {n.crown === "elected"
          ? `\nround winner${stampsTheta ? " — elected on difficulty-adjusted ability θ, not raw accuracy" : ""}`
          : n.crown === "uncontested"
            ? "\nthe round's only arm — it advances without an election"
            : n.isWinner
              ? "\nselected — its round has not closed yet"
              : ""}
        {retiredBy
          ? `\nretired — the run branched to ${shortFamilyTail(retiredBy)} and continued there; kept as the record of what ran`
          : ""}
        {ink ? "\na channel of the comparison — its colour here is the one its bar carries" : ""}
      </title>
      <line
        x1={n.x - d.candStub}
        y1={n.y}
        x2={n.x}
        y2={n.y}
        className={cx("lineage-stub", n.isWinner && "winner")}
        style={ink ? { stroke: ink } : undefined}
      />
      {/* A shape, not a colour: a channel's ink is an inline style no class rule can beat. */}
      {selected && (
        <circle cx={n.x} cy={n.y} r={NODE_R + 2.5} className="lineage-pick-mark" />
      )}
      {ink && (
        <circle
          cx={n.x}
          cy={n.y}
          r={NODE_R + 0.5}
          className="lineage-channel-mark"
          style={{ fill: ink }}
        />
      )}
      {d.labels && (
        <text
          x={n.x + 4}
          y={n.y + 3}
          className={cx("lineage-label", n.isWinner && "winner", selected && "selected")}
          style={ink ? { fill: ink } : undefined}
        >
          {n.candidateLabel} {invalidated ? "?" : fmtDisplayValue(shown, accuracy, theta)}
        </text>
      )}
      <rect
        x={n.x - d.candStub}
        y={n.y - 10}
        width={d.candStub + d.colW}
        height={20}
        fill="transparent"
      />
    </g>
  );
});

// The campaign's cladogram — the one served tree, rendered.
export function Forest({
  tree,
  valueByKey,
  thetaByKey,
  metric,
  expanded,
  onLaneActivate,
  ctx,
  d,
}: {
  tree: LineageNode;
  valueByKey: ReadonlyMap<string, number | null>;
  thetaByKey: ReadonlyMap<string, number | null>;
  metric: DisplayMetric;
  expanded: ReadonlySet<string>;
  // Toggles the lane in place; never changes the dashboard's selected cycle.
  onLaneActivate: (courseKey: string) => void;
  ctx: CladogramCtx;
  d: Density;
}) {
  const { viewedKey, isPicked, onPickCandidate, channels, clip } = ctx;
  const valOf = (n: RoundNodePos): number | null =>
    valueByKey.get(n.candKey) ?? null;
  const thetaOf = (n: RoundNodePos): number | null =>
    thetaByKey.get(n.candKey) ?? null;
  const { laneByKey, totalLaneRows, maxCol } = useMemo(
    () => layout(tree, expanded, clip && extentKeys(tree, clip)),
    [tree, expanded, clip],
  );
  const { nodes, segs } = useMemo(() => placeNodes(laneByKey, d), [laneByKey, d]);
  // Narrowest first: a node wears the ink of the first extent holding it.
  const extents = useMemo(
    () =>
      channels
        .flatMap((c) => {
          const keys = extentKeys(tree, c);
          return keys === null ? [] : [{ ink: c.ink, keys }];
        })
        .sort((a, b) => a.keys.size - b.keys.size),
    [channels, tree],
  );
  const inkOf = useCallback(
    (n: RoundNodePos): string | null =>
      extents.find((e) => e.keys.has(n.candKey))?.ink ?? null,
    [extents],
  );
  // The divergence marker rides the round's WINNER, so the alternative learns of itself here.
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
  const width = d.leftPad + (maxCol + 1) * d.colW + d.rightPad;

  const headerCols: number[] = [];
  for (let c = 1; d.leftPad + c * d.colW <= width - d.rightPad + d.colW / 2; c += 1) {
    headerCols.push(c);
  }

  const laneList = [...laneByKey.values()];
  const bandTop = (l: LaneLayout): number => TOP_PAD + l.laneOffset * LANE_H - LANE_H / 2 + 2;
  const bandH = (l: LaneLayout): number => l.laneSpan * LANE_H - 4;

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
            const x = d.leftPad + c * d.colW;
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

          {laneList.map((l) => {
            if (l.coursePathKey !== viewedKey) return null;
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

          {/* Painted before nodes so their clicks win; the row background falls through here. */}
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
                {...pressable(() => onLaneActivate(nodeKeyOf(course)))}
                aria-label={`${verb} ${courseName(course)}`}
                style={{ cursor: "pointer" }}
              >
                <title>
                  {course.id}
                  {`\n${course.course_kind}`}
                  {course.task ? ` · ${course.task}` : ""}
                  {course.trigger === "operator_steered"
                    ? ` · steered${course.steered_by ? ` by ${course.steered_by}` : ""}`
                    : ""}
                  {course.fork_direction === "supersede"
                    ? "\n↳ this branch is the line — the parent keeps what it was cut from"
                    : ""}
                  {course.fork_direction === "equivalent"
                    ? "\n≡ the cut reached nothing — this branch and its parent continue identically"
                    : ""}
                  {course.stop_reason ? ` · ${STOP_REASON_LABELS[course.stop_reason]}` : ""}
                  {course.best_accuracy != null ? ` · best ${fmtPct0(course.best_accuracy)}` : ""}
                  {isEmpty
                    ? "\nNo post-divergence rounds — use Clean up in the header to prune"
                    : `\n${roundCount} round(s) · click row to ${l.expanded ? "collapse" : "expand"}`}
                </title>
              </rect>
            );
          })}

          {nodes
            .filter((n) => !n.isExpanded)
            .map((n) => {
              const cycleSelected = n.coursePathKey === viewedKey;
              const layoutEntry = laneByKey.get(n.courseKey);
              const nodeCycleId = pathLeaf(n.coursePath).cycleId;
              const cycName = layoutEntry ? courseName(layoutEntry.course) : nodeCycleId;
              const rowLabelText = n.isLastInLane && layoutEntry ? cycName : null;
              // Inside an <svg>, so `<Hearts>` can't mount; `heartsText` is the same derivation.
              const laneHearts = layoutEntry
                ? heartsText(
                    layoutEntry.course.run_standing?.stalls_left,
                    layoutEntry.course.run_standing?.stalls_left_cap,
                  )
                : "";
              const isDivergence = n.divergence !== null;
              const isDivergent = n.divergent;
              const ink = inkOf(n);
              const shown = nodeMetric(metric, n.node.stamps_theta);
              return (
                <g
                  key={`n-${n.courseKey}-${n.round}`}
                  className={cx(
                    "family-cladogram-node",
                    cycleSelected && "selected",
                    isDivergent && "mask-divergent",
                    isDivergence && "mask-divergence",
                    ink && "channel",
                  )}
                  {...pressable(() => onLaneActivate(n.courseKey))}
                  aria-label={`Expand ${cycName}${ink ? ", holding a channel of the comparison" : ""}`}
                  style={{ cursor: "pointer" }}
                >
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={ink ? NODE_R + 1.5 : NODE_R}
                    className={`family-cladogram-dot kind-${n.courseKind}`}
                    style={ink ? { fill: ink } : undefined}
                  />
                  {isDivergence && (
                    <circle
                      cx={n.x}
                      cy={n.y}
                      r={NODE_R + 3}
                      className="family-cladogram-divergence-ring"
                    />
                  )}
                  {d.labels && (
                    <text
                      x={n.x}
                      y={n.y - 6}
                      className="family-cladogram-roundlabel"
                      textAnchor="middle"
                    >
                      R{n.round} {fmtDisplayValue(shown, valOf(n), thetaOf(n))}
                    </text>
                  )}
                  {d.labels && rowLabelText && (
                    <text x={n.x + 8} y={n.y + 3} className="family-cladogram-cyclelabel">
                      <tspan className="family-cladogram-glyph">
                        {KIND_GLYPH[n.courseKind]}
                        {TRIGGER_GLYPH[n.trigger] ?? ""}
                        {n.forkDirection ? DIRECTION_GLYPH[n.forkDirection] : ""}
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
                    {nodeCycleId} · R{n.round} · {fmtDisplayValue(shown, valOf(n), thetaOf(n))}
                    {shown !== "ability" && typeof thetaOf(n) === "number"
                      ? ` · ability θ ${fmtTheta(thetaOf(n))}`
                      : ""}
                    {n.candidateLabel ? `\n${n.candidateLabel}` : ""}
                    {isDivergence ? "\ndivergence under the scoring lens" : ""}
                    {isDivergent ? "\ncounterfactual under the scoring lens" : ""}
                    {ink ? "\na channel of the comparison — expand the lane to reach it" : ""}
                  </title>
                </g>
              );
            })}

          {nodes
            .filter((n) => n.isExpanded)
            .map((n) => (
              <CandidateNode
                key={`c-${n.candKey}`}
                n={n}
                accuracy={valOf(n)}
                theta={thetaOf(n)}
                metric={metric}
                selected={isPicked(n)}
                onPick={onPickCandidate}
                dimmed={n.divergent}
                alt={altIds.has(n.candidateId)}
                divergence={n.divergence !== null}
                invalidated={!!ctx.invalidated?.has(n.candidateId)}
                ink={inkOf(n)}
                d={d}
              />
            ))}

          {(d.labels ? nodes : [])
            .filter((n) => n.isExpanded && n.isLastInLane)
            .map((n) => {
              const course = laneByKey.get(n.courseKey)?.course;
              if (!course) return null;
              return (
                <text
                  key={`elabel-${n.courseKey}`}
                  x={n.x + d.candStub + 84}
                  y={n.y + 3}
                  className="family-cladogram-cyclelabel"
                >
                  <tspan className="family-cladogram-glyph">
                    {KIND_GLYPH[course.course_kind ?? "root"]}
                    {TRIGGER_GLYPH[course.trigger] ?? ""}
                    {course.fork_direction ? DIRECTION_GLYPH[course.fork_direction] : ""}
                  </tspan>
                  <tspan dx="4">{courseName(course)}</tspan>
                </text>
              );
            })}

        </svg>
    </div>
  );
}

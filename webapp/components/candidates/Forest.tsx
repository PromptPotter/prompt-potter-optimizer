"use client";
import { memo, useCallback, useMemo } from "react";
import { fmtPct0 } from "@/lib/format";
import {
  fmtHeadlineValue,
  headlineMetricLabel,
  nodeKeyOf,
  type HeadlineMetric,
} from "@/lib/derivations";
import { cx } from "@/lib/cx";
import { pathLeaf, shortFamilyTail } from "@/lib/ids";
import { heartsText } from "@/lib/derivations";
import type { LineageNode } from "@/lib/api";
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

// What a cladogram needs from the surface it is drawn on — injected, so the dashboard and the
// Compare tab draw the SAME tree and differ only in what a click there means. A second
// cladogram would be a second answer to "what descends from what".
// One comparison channel, as this drawing sees it: where it is anchored, and the ink it owns.
export interface CladogramChannel extends CladogramAnchor {
  // A `var()` reference, never a resolved value, so it repaints on a theme flip like everything
  // else in the SVG (`theme.ts::seriesVar`).
  ink: string;
}

export interface CladogramCtx {
  // The read course's encoded address; its lane band renders highlighted.
  viewedKey: string | null;
  // Is this the searchpoint the surface is reading at?
  isPicked: (n: RoundNodePos) => boolean;
  // A searchpoint clicked, carrying the value painted on it — the surface holds no overlay.
  onPickCandidate: (n: RoundNodePos, value: number | null) => void;
  // Every comparison channel on this drawing. A channel's EXTENT is everything at or before its
  // anchor's round-column — the drawing's own time axis — so the extents NEST, and a node wears
  // the ink of the NARROWEST one holding it. That is what makes a comparison something to look
  // at: the campaign's own channel colours the whole family, and a channel picked at round 2
  // takes the first three columns off it. Empty on a surface with no comparison on it.
  channels: readonly CladogramChannel[];
  // The channel this drawing is FOR — the family is cut to its extent. What came after a
  // searchpoint is no part of how that searchpoint came to be, and drawing it made every card
  // show the same picture. `null` draws the whole family.
  clip: CladogramAnchor | null;
  // Searchpoints whose CONFIGURATION the operator has changed, and everything descending from
  // one. Nothing ran at the edited value, so every measurement at or below it describes a
  // searchpoint that no longer exists — the drawing WITHDRAWS those numbers rather than showing
  // them under a changed setup. A different fact from `mask-divergent`, which recedes a
  // counterfactual the server actually computed; here there is nothing to compute. Empty on every
  // surface that offers no config editor. Candidate ids, the space `parent_id` speaks.
  invalidated?: ReadonlySet<string>;
}

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
  invalidated,
  ink,
  d,
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
  // A setting was changed at this point or above it, so nothing measured here describes it any
  // more (`CladogramCtx.invalidated`).
  invalidated: boolean;
  // The comparison channel anchored here, as its ink (`CladogramCtx.inkOf`).
  ink: string | null;
  d: Density;
}) {
  // Served (`superseded_by`): this attempt was replaced when the run branched away. It
  // recedes like a lens counterfactual but is a different fact — what the run DID, not
  // what a mask would have done — so it wears its own class and its own words.
  const retiredBy = n.retiredBy;
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
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`Round ${n.round} candidate ${n.candidateLabel}, ${invalidated ? "unknown — a setting was changed at or above this point" : `${headlineMetricLabel(metric)} ${fmtHeadlineValue(metric, accuracy, theta)}`}${n.isElected ? ", round winner" : ""}${ink ? ", a channel of the comparison" : ""}${retiredBy ? ", retired — the run branched away and continued elsewhere" : ""}${divergence ? ", divergence point under the lens" : ""}${alt ? ", would be elected under the scoring lens" : ""}${dimmed ? ", counterfactual under the scoring lens" : ""}`}
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
        {n.candidateLabel} ·{" "}
        {invalidated
          ? "unknown"
          : fmtHeadlineValue(metric, accuracy, theta)}
        {!invalidated && metric !== "ability" && typeof theta === "number"
          ? ` · ability θ ${theta.toFixed(2)}`
          : ""}
        {invalidated
          ? "\na setting was changed here or above — nothing ran at that value, so this point's numbers describe a searchpoint it no longer is"
          : ""}
        {n.isElected
          ? "\nround winner — elected on difficulty-adjusted ability θ, not raw accuracy"
          : n.isWinner
            ? "\nthe round's only arm — it advances without an election"
            : ""}
        {retiredBy
          ? `\nretired — the run branched to ${shortFamilyTail(retiredBy)} and continued there; kept as the record of what ran`
          : ""}
        {ink ? "\na channel of the comparison — its colour here is the one its bar carries" : ""}
      </title>
      {/* The alternative candidate is marked by its own branch line glowing red
          (`.mask-alt .lineage-stub`) — no glyph. */}
      <line
        x1={n.x - d.candStub}
        y1={n.y}
        x2={n.x}
        y2={n.y}
        className={cx("lineage-stub", n.isWinner && "winner")}
        style={ink ? { stroke: ink } : undefined}
      />
      {/* SELECTED is a shape for the same two reasons the channel mark below is, and it needed
          both: at DENSE the stub is a few px and unlabelled, so a stroke colour is invisible —
          and on a channel node the ink is an INLINE style, which no class rule can beat. Drawn
          first so a node that is both wears the ring outside its channel dot. */}
      {selected && (
        <circle cx={n.x} cy={n.y} r={NODE_R + 2.5} className="lineage-pick-mark" />
      )}
      {/* The channel's own mark. It has to be a SHAPE, not the stub's colour alone: at DENSE the
          stub is a few px and unlabelled, which is exactly the width two channels are compared
          at. The `<title>` and the aria-label carry the same fact in words. */}
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
          {n.candidateLabel} {invalidated ? "?" : fmtHeadlineValue(metric, accuracy, theta)}
        </text>
      )}
      {/* Invisible click target: the candidate's own slot — its stub plus the one
          column-width its label occupies before the next round's node. */}
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

// The campaign's cladogram — the ONE served tree, rendered. Its own <svg> so lane
// math is never reconciled across surfaces.
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
  // The served genealogy's root course. Nodes alternate course → candidate →
  // (course | sample), so forks and L4 inner runs need no special case here.
  tree: LineageNode;
  // Live per-candidate percent metric (accuracy/composite), keyed
  // Keyed by the candidate's address (`nodeKeyOf`) — painted onto nodes outside the geometry memo so a
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
  onLaneActivate: (courseKey: string) => void;
  // Where this cladogram is drawn and what a searchpoint click does there.
  ctx: CladogramCtx;
  // How much width it may spend. `DENSE` is what lets two trees sit beside each other.
  d: Density;
}) {
  const { viewedKey, isPicked, onPickCandidate, channels, clip } = ctx;
  // The live fitness painted on a node — the `valueByKey` overlay, looked up by
  // the same candidate identity the bars use. Outside the layout memo, so it
  // updates each poll without re-flowing the tree.
  const valOf = (n: RoundNodePos): number | null =>
    valueByKey.get(n.candKey) ?? null;
  // Difficulty-adjusted ability for the node tooltip — what the winner was elected on.
  const thetaOf = (n: RoundNodePos): number | null =>
    thetaByKey.get(n.candKey) ?? null;
  // Stable across a poll tick, because `CandidateNode` is memoized and an inline arrow here
  // re-renders every node on every tick.
  const onPick = useCallback(
    (n: RoundNodePos) => onPickCandidate(n, valueByKey.get(n.candKey) ?? null),
    [onPickCandidate, valueByKey],
  );
  // Layout is pure and the tree changes identity only on a real refetch, so this
  // memo re-runs only on a shape change (new round / candidate / winner flip / lane
  // toggle), never on a bare 2 s poll — the value overlay rides outside it.
  //
  const { laneByKey, totalLaneRows, maxCol } = useMemo(
    () => layout(tree, expanded, clip && extentKeys(tree, clip)),
    [tree, expanded, clip],
  );
  const { nodes, segs } = useMemo(() => placeNodes(laneByKey, d), [laneByKey, d]);
  // Each channel's extent, NARROWEST FIRST — so the first one holding a node is the one whose ink
  // it wears, and a channel this tree does not hold is simply not in the list. The SAME set the
  // cut is made from, so what a card draws and what it colours cannot disagree.
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
  const width = d.leftPad + (maxCol + 1) * d.colW + d.rightPad;

  // Round-number header — one label per column across the whole family.
  const headerCols: number[] = [];
  for (let c = 1; d.leftPad + c * d.colW <= width - d.rightPad + d.colW / 2; c += 1) {
    headerCols.push(c);
  }

  const laneList = [...laneByKey.values()];
  // Band y/height for a lane (covers all its rows when expanded).
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

          {/* Selected-lane highlight — covers the whole band when expanded. */}
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
                onClick={() => onLaneActivate(nodeKeyOf(course))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onLaneActivate(nodeKeyOf(course));
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
                  {course.fork_direction === "supersede"
                    ? "\n↳ this branch is the line — the parent keeps what it was cut from"
                    : ""}
                  {course.fork_direction === "equivalent"
                    ? "\n≡ the cut reached nothing — this branch and its parent continue identically"
                    : ""}
                  {course.status ? ` · ${course.status}` : ""}
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
              const cycleSelected = n.coursePathKey === viewedKey;
              const layoutEntry = laneByKey.get(n.courseKey);
              const nodeCycleId = pathLeaf(n.coursePath).cycleId;
              const cycName = layoutEntry ? courseName(layoutEntry.course) : nodeCycleId;
              const rowLabelText = n.isLastInLane && layoutEntry ? cycName : null;
              // The lane's ♥ bank, as glyphs — the cladogram is an <svg>, so the shared
              // <Hearts> component can't mount here; `heartsText` is the same derivation
              // rendered as text. Empty string when the cycle isn't in lives mode.
              const laneHearts = layoutEntry
                ? heartsText(layoutEntry.course.hearts, layoutEntry.course.lives_cap)
                : "";
              const isDivergence = n.divergence !== null;
              const isDivergent = n.divergent;
              // A collapsed lane still carries its channels — the round the comparison is
              // anchored on is a summary dot here, and leaving it black is what made an
              // unexpanded seed indistinguishable from every other lane on the drawing.
              const ink = inkOf(n);
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
                  role="button"
                  tabIndex={0}
                  aria-label={`Expand ${cycName}${ink ? ", holding a channel of the comparison" : ""}`}
                  onClick={() => onLaneActivate(n.courseKey)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onLaneActivate(n.courseKey);
                    }
                  }}
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
                      R{n.round} {fmtHeadlineValue(metric, valOf(n), thetaOf(n))}
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
                    {nodeCycleId} · R{n.round} · {fmtHeadlineValue(metric, valOf(n), thetaOf(n))}
                    {metric !== "ability" && typeof thetaOf(n) === "number"
                      ? ` · ability θ ${thetaOf(n)!.toFixed(2)}`
                      : ""}
                    {n.candidateLabel ? `\n${n.candidateLabel}` : ""}
                    {isDivergence ? "\ndivergence under the scoring lens" : ""}
                    {isDivergent ? "\ncounterfactual under the scoring lens" : ""}
                    {ink ? "\na channel of the comparison — expand the lane to reach it" : ""}
                  </title>
                </g>
              );
            })}

          {/* Expanded candidate nodes (lineage-style stubs). */}
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
                onPick={onPick}
                dimmed={n.divergent}
                alt={altIds.has(n.candidateId)}
                divergence={n.divergence !== null}
                invalidated={!!ctx.invalidated?.has(n.candidateId)}
                ink={inkOf(n)}
                d={d}
              />
            ))}

          {/* Expanded lanes carry their cycle label beside the last winner. */}
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

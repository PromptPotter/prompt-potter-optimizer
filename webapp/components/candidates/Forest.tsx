import { memo, useCallback, useMemo, useRef } from "react";
import { fmtUnit } from "@/lib/format";
import { useWidth } from "@/lib/hooks/useWidth";
import { cx } from "@/lib/cx";
import { pressable } from "@/components/ui";
import { pathLeaf, pathOfAddress } from "@/lib/ids";
import { figureOf } from "@/lib/derivations";
import {
  type CycleMint,
  type CycleSpawn,
  type SubtreeCampaign,
  DISPLAY_METRIC_INFO,
  type DisplayMetric,
} from "@/lib/api/types";
import {
  DIRECTION_GLYPH,
  HEADER_H,
  KIND_GLYPH,
  TRIGGER_GLYPH,
  LANE_H,
  NODE_R,
  TOP_PAD,
  extentKeys,
  laneName,
  layout,
  type CladogramAnchor,
  type Density,
  type LaneLayout,
  type RoundNodePos,
} from "./lineage-tree";

export interface CladogramChannel extends CladogramAnchor {
  // A `var()` reference (`theme.ts::seriesVar`), never a resolved value.
  ink: string;
}

export interface CladogramCtx {
  viewedKey: string | null;
  isPicked: (n: RoundNodePos) => boolean;
  onPickCandidate: (n: RoundNodePos) => void;
  channels: readonly CladogramChannel[];
  clip: CladogramAnchor | null;
  invalidated?: ReadonlySet<string>;
}

function SpawnGlyph({ kind, spawn }: { kind: CycleMint; spawn: CycleSpawn | null }) {
  return (
    <>
      {KIND_GLYPH[kind]}
      {spawn === null ? "" : `${TRIGGER_GLYPH[spawn.trigger]}${DIRECTION_GLYPH[spawn.direction]}`}
    </>
  );
}

const CandidateNode = memo(function CandidateNode({
  n,
  level,
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
  level: number | null;
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
      aria-label={`Round ${n.round} candidate ${n.label}, ${invalidated ? "unknown — a setting was changed at or above this point" : `${DISPLAY_METRIC_INFO[metric].label} ${fmtUnit(DISPLAY_METRIC_INFO[metric].unit,level)}`}${n.crown === "elected" ? ", round winner" : ""}${ink ? ", a channel of the comparison" : ""}${retiredBy ? ", retired — the run branched away and continued elsewhere" : ""}${divergence ? ", divergence point under the lens" : ""}${alt ? ", would be elected under the scoring lens" : ""}${dimmed ? ", counterfactual under the scoring lens" : ""}`}
      style={{ cursor: "pointer" }}
    >
      <title>
        {n.label} ·{" "}
        {invalidated
          ? "unknown"
          : fmtUnit(DISPLAY_METRIC_INFO[metric].unit,level)}
        {n.node.facts.variations
          .map((v) => `\n${v.node} (${v.mode}): ${v.loci.join(", ") || "nothing"}`)
          .join("")}
        {invalidated
          ? "\na setting was changed here or above — nothing ran at that value, so this point's numbers describe a searchpoint it no longer is"
          : ""}
        {n.crown === "elected"
          ? "\nround winner"
          : n.crown === "uncontested"
            ? "\nthe round's only arm — it advances without an election"
            : n.isWinner
              ? "\nselected — its round has not closed yet"
              : ""}
        {retiredBy
          ? `\n${retiredBy} — the run branched there and continued; kept as the record of what ran`
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
          {n.label} {invalidated ? "?" : fmtUnit(DISPLAY_METRIC_INFO[metric].unit,level)}
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

export function Forest({
  tree,
  valueByKey,
  metric,
  expanded,
  onLaneActivate,
  ctx,
}: {
  tree: SubtreeCampaign;
  valueByKey: ReadonlyMap<string, number | null>;
  metric: DisplayMetric;
  expanded: ReadonlySet<string>;
  onLaneActivate: (cycleKey: string) => void;
  ctx: CladogramCtx;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const boxW = useWidth(boxRef);
  const { viewedKey, isPicked, onPickCandidate, channels, clip } = ctx;
  const valOf = (n: RoundNodePos): number | null =>
    valueByKey.get(n.candKey) ?? null;
  const { laneByKey, ends, width, totalLaneRows, maxCol, d, nodes, segs } = useMemo(
    () => layout({ freed: tree, expanded, keep: clip && extentKeys(tree, clip), boxW }),
    [tree, expanded, clip, boxW],
  );
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
  // By address: one arm key re-proposed on another branch is another candidate.
  const alternatives = useMemo(
    () => new Set(nodes.flatMap((n) => n.divergence?.alternative ?? [])),
    [nodes],
  );
  const height = TOP_PAD + totalLaneRows * LANE_H + 8;

  const headerCols = Array.from({ length: maxCol }, (_, i) => i + 1);

  const laneList = [...laneByKey.values()];
  const bandTop = (l: LaneLayout): number => TOP_PAD + l.laneOffset * LANE_H - LANE_H / 2 + 2;
  const bandH = (l: LaneLayout): number => l.laneSpan * LANE_H - 4;

  return (
    <div ref={boxRef} className="family-cladogram-forest">
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
              className={cx("family-cladogram-branch", s.variant !== "chain" && s.variant)}
            >
              {s.note && <title>{s.note}</title>}
            </line>
          ))}

          {laneList.map((l) => {
            if (l.cyclePathKey !== viewedKey) return null;
            return (
              <rect
                key={`hl-${l.cyclePathKey}`}
                x={0}
                y={bandTop(l)}
                width={width}
                height={bandH(l)}
                className="family-cladogram-lane-selected"
              />
            );
          })}

          {/* Painted before the nodes so their clicks win. */}
          {laneList.map((l) => {
            const cycle = l.cycle;
            const { spawn } = cycle.facts;
            const selection = figureOf(cycle, "selection");
            const isEmpty = l.candidates.length === 0;
            const roundCount = new Set(l.candidates.map((c) => c.facts.reading.arm.round)).size;
            const verb = l.expanded ? "Collapse" : "Expand";
            return (
              <rect
                key={`lanehit-${l.cyclePathKey}`}
                x={0}
                y={bandTop(l)}
                width={width}
                height={bandH(l)}
                className="family-cladogram-lane-hit"
                {...pressable(() => onLaneActivate(cycle.address))}
                aria-label={`${verb} ${laneName(cycle)}`}
                style={{ cursor: "pointer" }}
              >
                <title>
                  {pathLeaf(pathOfAddress(cycle.address)).cycleId}
                  {`\n${cycle.facts.mint}`}
                  {cycle.facts.task ? ` · ${cycle.facts.task}` : ""}
                  {spawn?.trigger === "operator_steered"
                    ? ` · steered${spawn.issued_by ? ` by ${spawn.issued_by}` : ""}`
                    : ""}
                  {spawn?.direction === "supersede"
                    ? "\n↳ this branch is the line — the parent keeps what it was cut from"
                    : ""}
                  {` · ${figureOf(cycle, "state")?.value}`}
                  {selection ? ` · ${selection.label}: ${selection.value}` : ""}
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
              const cycleSelected = n.cyclePathKey === viewedKey;
              const layoutEntry = laneByKey.get(n.cycleKey);
              const nodeCycleId = pathLeaf(n.cyclePath).cycleId;
              const cycName = layoutEntry ? laneName(layoutEntry.cycle) : nodeCycleId;
              const isDivergence = n.divergence !== null;
              const isDivergent = n.divergent;
              const ink = inkOf(n);
              return (
                <g
                  key={`n-${n.cycleKey}-${n.round}`}
                  className={cx(
                    "family-cladogram-node",
                    cycleSelected && "selected",
                    isDivergent && "mask-divergent",
                    isDivergence && "mask-divergence",
                    ink && "channel",
                  )}
                  {...pressable(() => onLaneActivate(n.cycleKey))}
                  aria-label={`Expand ${cycName}${ink ? ", holding a channel of the comparison" : ""}`}
                  style={{ cursor: "pointer" }}
                >
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={ink ? NODE_R + 1.5 : NODE_R}
                    className={`family-cladogram-dot kind-${n.cycleKind}`}
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
                      R{n.round} {fmtUnit(DISPLAY_METRIC_INFO[metric].unit,valOf(n))}
                    </text>
                  )}
                  <title>
                    {nodeCycleId} · R{n.round} · {fmtUnit(DISPLAY_METRIC_INFO[metric].unit,valOf(n))}
                    {n.label ? `\n${n.label}` : ""}
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
                level={valOf(n)}
                metric={metric}
                selected={isPicked(n)}
                onPick={onPickCandidate}
                dimmed={n.divergent}
                alt={alternatives.has(n.candKey)}
                divergence={n.divergence !== null}
                invalidated={!!ctx.invalidated?.has(n.candKey)}
                ink={inkOf(n)}
                d={d}
              />
            ))}

          {ends.map(({ lane: { cycle, cyclePathKey }, x, y, name, hearts }) => {
            // Inside an <svg>, so `<Hearts>` can't mount; the served figure is the same pips as text.
            return (
              <text
                key={`elabel-${cyclePathKey}`}
                x={x}
                y={y + 3}
                className={cx("family-cladogram-cyclelabel", cyclePathKey === viewedKey && "selected")}
              >
                <tspan className="family-cladogram-glyph">
                  <SpawnGlyph kind={cycle.facts.mint} spawn={cycle.facts.spawn} />
                </tspan>
                <tspan dx="4">{name}</tspan>
                {hearts && (
                  <tspan dx="6" className="family-cladogram-hearts">
                    {hearts}
                  </tspan>
                )}
              </text>
            );
          })}

        </svg>
    </div>
  );
}

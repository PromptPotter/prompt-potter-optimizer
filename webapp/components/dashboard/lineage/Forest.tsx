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
import { useLineageOverlay } from "@/lib/lineage-overlay";
import { heartsText } from "@/lib/derivations";
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
  type CycleNode,
  type DetailByCycle,
  type LaneLayout,
  type RoundNodePos,
} from "./layout";

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

// One session's cladogram — its own <svg> so cross-session lane math
// never has to be reconciled. The session header appears only when the
// campaign has more than one session.
export function Forest({
  tree,
  campaignId,
  cycleId,
  detailByCycle,
  valueByKey,
  thetaByKey,
  metric,
  expanded,
  onLaneActivate,
  onSelectCycle,
}: {
  tree: CycleNode;
  campaignId: string;
  cycleId: string | null;
  detailByCycle: DetailByCycle;
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
  // Served scoring-mask overlay, read straight from its provider (the single
  // source the fitness panel + lineage card share), keyed by `{cycle_id}::r{round}`.
  // `divergenceByKey`: a divergence node → the one-step alternative candidate id
  // (or null = origin would hold). `divergentKeys`: the dimmed counterfactual
  // subtree. Both empty when no lens is active. Rendered, never recomputed (R-36).
  const { divergenceByKey, divergentKeys } = useLineageOverlay();
  const nodeKey = (cid: string, round: number): string => `${cid}::r${round}`;
  // The live fitness painted on a node — the `valueByKey` overlay, looked up by
  // the same candidate identity the bars use. Outside the layout memo, so it
  // updates each poll without re-flowing the tree.
  const valOf = (n: RoundNodePos): number | null =>
    valueByKey.get(`${n.cycleId}::${n.candidateId}`) ?? null;
  // Difficulty-adjusted ability for the node tooltip — what the winner was elected on.
  const thetaOf = (n: RoundNodePos): number | null =>
    thetaByKey.get(`${n.cycleId}::${n.candidateId}`) ?? null;
  // Layout is pure + the inputs are content-stabilized upstream, so this memo
  // re-runs only on a real shape change (new round / candidate / winner flip /
  // lane toggle), never on a bare 2 s poll.
  const { laneByCycle, totalLaneRows, maxCol } = useMemo(
    () => layout(tree, detailByCycle, expanded),
    [tree, detailByCycle, expanded],
  );
  const { nodes, segs } = useMemo(() => placeNodes(laneByCycle), [laneByCycle]);
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

  // Candidate click: inspect that searchpoint. A candidate in a non-selected
  // lane first navigates the dashboard to its cycle (so inspector/samples
  // follow, and the SelectionProvider doesn't clear the candidate on the cycle
  // change); within the selected lane it picks/toggles the candidate directly.
  const onPickCandidate = (n: RoundNodePos): void => {
    if (n.cycleId !== cycleId) {
      onSelectCycle(campaignId, n.cycleId);
      return;
    }
    const isSel =
      candidate != null && candidate.round === n.round && candidate.candidate_id === n.candidateId;
    setSelectionForCandidate(
      isSel
        ? null
        : {
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
            if (l.cycle.cycle_id !== cycleId) return null;
            return (
              <rect
                key={`hl-${l.cycle.cycle_id}`}
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
            const cyc = l.cycle;
            const isEmpty = cyc.rounds.length === 0;
            const verb = l.expanded ? "Collapse" : "Expand";
            return (
              <rect
                key={`lanehit-${cyc.cycle_id}`}
                x={0}
                y={bandTop(l)}
                width={width}
                height={bandH(l)}
                className="family-cladogram-lane-hit"
                role="button"
                tabIndex={0}
                aria-label={`${verb} ${cyc.sibling_kind === "root" ? cyc.dataset_name || cyc.cycle_id : shortFamilyTail(cyc.cycle_id)}`}
                onClick={() => onLaneActivate(cyc.cycle_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onLaneActivate(cyc.cycle_id);
                  }
                }}
                style={{ cursor: "pointer" }}
              >
                <title>
                  {cyc.cycle_id}
                  {`\n${cyc.sibling_kind}`}
                  {cyc.trigger === "operator_steered"
                    ? ` · steered${cyc.steered_by ? ` by ${cyc.steered_by}` : ""}`
                    : ""}
                  {cyc.status ? ` · ${cyc.status}` : ""}
                  {cyc.best_accuracy != null ? ` · best ${fmtPct0(cyc.best_accuracy)}` : ""}
                  {isEmpty
                    ? "\nNo post-divergence rounds — use Clean up in the header to prune"
                    : `\n${cyc.rounds.length} round(s) · click row to ${l.expanded ? "collapse" : "expand"}`}
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
              const cycName = layoutEntry
                ? layoutEntry.cycle.sibling_kind === "root"
                  ? layoutEntry.cycle.dataset_name || layoutEntry.cycle.cycle_id
                  : shortFamilyTail(layoutEntry.cycle.cycle_id)
                : n.cycleId;
              const rowLabelText = n.isLastInLane && layoutEntry ? cycName : null;
              // The lane's ♥ bank, as glyphs — the cladogram is an <svg>, so the shared
              // <Hearts> component can't mount here; `heartsText` is the same derivation
              // rendered as text. Empty string when the cycle isn't in lives mode.
              const laneHearts = layoutEntry
                ? heartsText(layoutEntry.cycle.hearts, layoutEntry.cycle.lives_cap)
                : "";
              const key = nodeKey(n.cycleId, n.round);
              const isDivergence = divergenceByKey.has(key);
              const isDivergent = divergentKeys.has(key);
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
                    className={`family-cladogram-dot kind-${n.sibling_kind}`}
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
                        {KIND_GLYPH[n.sibling_kind]}
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
            .map((n) => {
              const key = nodeKey(n.cycleId, n.round);
              return (
                <CandidateNode
                  key={`c-${n.cycleId}-${n.round}-${n.candidateId}`}
                  n={n}
                  accuracy={valOf(n)}
                  theta={thetaOf(n)}
                  metric={metric}
                  selected={
                    n.cycleId === cycleId &&
                    candidate != null &&
                    candidate.round === n.round &&
                    candidate.candidate_id === n.candidateId
                  }
                  onPick={onPickCandidate}
                  dimmed={divergentKeys.has(key)}
                  alt={divergenceByKey.get(key) === n.candidateId && n.candidateId !== ""}
                  divergence={divergenceByKey.has(key) && n.isWinner}
                />
              );
            })}

          {/* Expanded lanes carry their cycle label beside the last winner. */}
          {nodes
            .filter((n) => n.isExpanded && n.isLastInLane)
            .map((n) => {
              const cyc = laneByCycle.get(n.cycleId)?.cycle;
              if (!cyc) return null;
              const label =
                cyc.sibling_kind === "root"
                  ? cyc.dataset_name || cyc.cycle_id
                  : shortFamilyTail(cyc.cycle_id);
              return (
                <text
                  key={`elabel-${n.cycleId}`}
                  x={n.x + CAND_STUB + 84}
                  y={n.y + 3}
                  className="family-cladogram-cyclelabel"
                >
                  <tspan className="family-cladogram-glyph">
                    {KIND_GLYPH[cyc.sibling_kind]}
                    {TRIGGER_GLYPH[cyc.trigger] ?? ""}
                  </tspan>
                  <tspan dx="4">{label}</tspan>
                </text>
              );
            })}

        </svg>
    </div>
  );
}

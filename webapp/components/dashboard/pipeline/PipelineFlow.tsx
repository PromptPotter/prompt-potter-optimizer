"use client";
import type { ReactNode } from "react";
import {
  nodeKind,
  nodeSubLabel,
  type PipelineView,
  type PipelineViewNode,
} from "@/components/workflow";
import type { NodeReach } from "@/lib/api";
import type { NodeScope } from "@/lib/SelectionContext";
import type { PipelineStatus } from "@/lib/types";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useSelection } from "@/lib/SelectionContext";
import {
  agentLabel,
  cycleOf,
  interiorNodes,
  layoutGrid,
  liveObserveConfig,
} from "@/lib/derivations";
import { TERMS } from "@/lib/terms";
import { Icon, pressable } from "@/components/ui";
import { cx } from "@/lib/cx";

// One level of the pipeline stack; `PipelineStack` owns the chain of levels.

// The target LLM is called exactly while this OPTIMIZER node is active. Compare it against
// the served `active_node` only, never against a target node's id.
const OPTIMIZER_SCORING_NODE = "l1_score";

// Each kind is styled in `chat.css`; the wire's `kind` is a bare string, so an unknown one
// draws as `forward`.
const EDGE_KINDS: readonly string[] = ["forward", "loop", "escalate", "directive"];

const LABEL_BELOW_EXTENT = 38;

const ATTACH_ICON = (
  <Icon size={28} viewBox="0 0 20 20" strokeWidth={1.4}>
    <polyline points="18 10 13 10 11.5 12.5 8.5 12.5 7 10 2 10" />
    <path d="M4.6 4.4 2 10v5a1.5 1.5 0 0 0 1.5 1.5h13a1.5 1.5 0 0 0 1.5-1.5v-5l-2.6-5.6a1.5 1.5 0 0 0-1.36-.9H5.96a1.5 1.5 0 0 0-1.36.9Z" />
  </Icon>
);

const ANSWER_ICON = (
  <Icon size={28} viewBox="0 0 20 20" strokeWidth={1.4}>
    <path d="M5 2h6l4 4v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" />
    <path d="M11 2v5h4" />
    <path d="M6.5 12.5h6" />
    <path d="m10.5 10.5 2.5 2-2.5 2" />
  </Icon>
);

const LLM_ICON = (
  <Icon size={30} strokeWidth={1.6}>
    <path d="M12 2.5 6.5 18h11Z" fill="currentColor" fillOpacity="0.18" />
    <path d="M5 18c2.4 1.6 4.7 2 7 2s4.6-.4 7-2" />
    <path d="M5 18h14" />
    <path
      d="m13.6 8.4.55 1.55 1.55.55-1.55.55-.55 1.55-.55-1.55-1.55-.55 1.55-.55Z"
      fill="currentColor"
    />
    <circle cx="10.2" cy="13.2" r="0.7" fill="currentColor" />
  </Icon>
);

// Never fabricate a node here: a failed read would pass for a real single-LLM pipeline.
function PipelinePlaceholder({ status }: { status: PipelineStatus }) {
  const [label, value, hint] =
    status === "error"
      ? (["Pipeline", "unavailable", "Couldn't read this campaign's dataset."] as const)
      : status === "loading"
        ? (["Pipeline", "loading…", undefined] as const)
        : (["Pipeline", "none", "No dataset bound to this campaign."] as const);
  return (
    <div
      className="wf-hero-node"
      aria-label={`Pipeline ${value}`}
      aria-busy={status === "loading" || undefined}
      title={hint}
    >
      <div className="head">
        <div className="ico">{LLM_ICON}</div>
        <div className="lbl">{label}</div>
      </div>
      <div className="val">{value}</div>
    </div>
  );
}

interface BoxProps {
  view: PipelineView;
  connector: string | null;
  activeNode: string | null;
  isLive: boolean;
  // SERVED, never summed here. A node absent from the map is UNKNOWN — never draw it as shut.
  reach: Record<string, NodeReach> | null;
  // Null makes every node inert: a level with no detail panel offers no click.
  scope: NodeScope | null;
  // `onIsolate` is null where no level below is on screen; the node then selects like any other.
  nest: { node: string; onIsolate: (() => void) | null } | null;
  compact: boolean;
  models: { by: Record<string, string | null>; loading: boolean } | null;
}

// One box at every size, single-node included.
function PipelineBox({
  view,
  connector,
  activeNode,
  isLive,
  reach: reachByNode,
  scope,
  nest,
  compact,
  models,
}: BoxProps) {
  const { node: selected, setSelectionForNode: setSelected } = useSelection();
  const { dash } = useDashboard();
  const interior = interiorNodes(view);
  const CELL_W = compact ? 44 : models ? 132 : 72;
  const CELL_W_OPEN = 132;
  // Not 0: a squashed cell still shows its dot and takes a tap, so the bonus shrinks instead.
  const CELL_W_MIN = 20;
  const ROW_H = compact ? 26 : 70;
  const RADIUS = compact ? 5.5 : 7;
  const cy = compact ? 13 : 14;
  const LABEL_BLOCK = compact ? RADIUS : 34;
  const ROW_GAP = compact ? 18 : 38;
  // Per-dot band on a grid: a full-height rect each would leave only the last-drawn reachable.
  const HIT_BAND = compact ? 24 : 40;
  const isSel = (id: string) => scope != null && selected?.scope === scope && selected.id === id;
  const nestAt = (id: string) => (nest != null && id === nest.node ? nest : null);
  const activate = (id: string) => {
    const here = nestAt(id);
    if (here?.onIsolate) return here.onIsolate();
    if (scope == null) return;
    setSelected(isSel(id) ? null : { id, scope });
  };

  const cycle = cycleOf(interior, view.edges);

  // `derive_pipeline_view` puts every node of a loopless view on tier 0, so `rank` alone
  // columns it.
  const cols = Math.max(interior.length, 1);

  // Siblings give exactly the open cell's bonus, so expanding never pushes the tail out.
  const others = Math.max(cols - 1, 0);
  const givable = others * (CELL_W - CELL_W_MIN);
  const selectedCol = interior.find((n) => isSel(n.id))?.rank ?? -1;
  const bonus =
    !compact && selectedCol >= 0 ? Math.min(CELL_W_OPEN - CELL_W, givable) : 0;
  const shrink = others > 0 ? bonus / others : 0;
  const widths = Array.from({ length: cols }, (_, i) =>
    i === selectedCol ? CELL_W + bonus : CELL_W - shrink,
  );
  const offsets: number[] = [];
  widths.reduce((acc, w, i) => {
    offsets[i] = acc;
    return acc + w;
  }, 0);
  const railW = cols * CELL_W;
  const colX = (i: number) => (offsets[i] ?? 0) + (widths[i] ?? CELL_W) / 2;

  // A looping graph folds onto a grid on EVERY surface, compact included — one graph, one
  // picture. A chain keeps the rail.
  const ring = cycle.length
    ? layoutGrid(interior, cycle, {
        cell: CELL_W,
        // Two pixels short and the model sublabel is shaved off silently.
        rowH: compact ? 24 : LABEL_BELOW_EXTENT + 34,
        padTop: compact ? 13 : 16,
        padBottom: compact ? 13 : LABEL_BELOW_EXTENT,
      })
    : null;

  const totalW = ring ? ring.width : railW;
  const canvasH = ring ? ring.height : ROW_H;
  const at = (n: PipelineViewNode) =>
    ring?.pos.get(n.id) ?? { x: colX(n.rank), y: cy, muted: false };
  const posOf = new Map(interior.map((n) => [n.id, at(n)] as const));

  // One ribbon per SERVED edge only — never between array-adjacent nodes.
  const edgeD = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const [x1, y1] = [a.x + (dx / len) * RADIUS, a.y + (dy / len) * RADIUS];
    const [x2, y2] = [b.x - (dx / len) * RADIUS, b.y - (dy / len) * RADIUS];
    const mx = (x1 + x2) / 2;

    // Between rows the upper node joins below its label block, never through its text.
    if (Math.abs(dy) > 1) {
      const down = a.y < b.y;
      const [upper, lower] = down ? [a, b] : [b, a];
      const top = { x: upper.x, y: upper.y + LABEL_BLOCK };
      const bottom = { x: lower.x, y: lower.y - RADIUS };
      const [p0, p1] = down ? [top, bottom] : [bottom, top];
      return `M ${p0.x} ${p0.y} Q ${(p0.x + p1.x) / 2} ${(p0.y + p1.y) / 2} ${p1.x} ${p1.y}`;
    }
    // A step BACK bows up into the gap: sagging would cross every spanned label.
    const rise = Math.min(len * 0.16, ROW_GAP * 0.78);
    const bow = dx > 0 ? 6 : -rise;
    return `M ${x1} ${y1} C ${mx} ${y1 + bow} ${mx} ${y2 + bow} ${x2} ${y2}`;
  };

  // An edge onto an `io` end (dropped by `interiorNodes`) still draws, as a terminal stub.
  const terminalD = (p: { x: number; y: number }) =>
    `M ${p.x + RADIUS} ${p.y} L ${p.x + CELL_W * 0.5} ${p.y}`;

  const labelLines = (label: string) =>
    label.includes("_") ? label.split("_") : [label];

  // `active_node` speaks for the optimizer, so it names a node here only on a self-optimizing
  // campaign; otherwise the whole chip pulses. Never both.
  const namedHere = isLive && interior.some((n) => n.id === activeNode);
  const calling = isLive && activeNode === OPTIMIZER_SCORING_NODE && !namedHere;

  const sole = interior.length === 1 ? interior[0] : undefined;
  // The live record alone; never fall back to the config row, which answers for the root.
  const liveCfg = sole ? liveObserveConfig(dash)?.config[sole.id] : null;
  const liveModel =
    liveCfg && typeof liveCfg === "object"
      ? (liveCfg as Record<string, unknown>).model
      : null;
  const soleModel = typeof liveModel === "string" && liveModel ? liveModel : null;
  if (sole) {
    const isSelected = isSel(sole.id);
    const soleNest = nestAt(sole.id);
    return (
      <div className={cx("wf-hero-node", "llm", isSelected && "selected", calling && "active")}>
        {connector && <div className="wf-hero-multi-tag">{connector}</div>}
        <button
          type="button"
          className="wf-hero-sole"
          aria-pressed={soleNest?.onIsolate ? undefined : isSelected}
          aria-label={
            soleNest?.onIsolate
              ? `${sole.label} — show what it runs, alone`
              : `Node: ${sole.label}`
          }
          disabled={scope == null && soleNest?.onIsolate == null}
          onClick={() => activate(sole.id)}
        >
          {/* Spans, not divs: a `<button>` takes phrasing content only. */}
          <span className="head">
            <span className="ico">{LLM_ICON}</span>
            <span className="lbl">{sole.label}</span>
          </span>
          <span className="val">{calling ? (soleModel ?? "running") : "idle"}</span>
        </button>
      </div>
    );
  }

  return (
    <div className={cx("wf-hero-node", "llm", "wf-hero-node-multi", calling && "active")}>
      {connector && <div className="wf-hero-multi-tag">{connector}</div>}
      <div className="wf-hero-multi-rail">
      {/* min-width floors the scaling: below ~44px a cell is unreadable, so the rail scrolls. */}
      <svg
        viewBox={`0 0 ${totalW} ${canvasH}`}
        preserveAspectRatio="xMidYMid meet"
        width="100%"
        height={canvasH}
        style={{ minWidth: `${totalW}px` }}
        role="img"
        aria-label="Pipeline graph"
        className="wf-hero-multi-svg"
      >
        <defs>
          {EDGE_KINDS.map((k) => (
            <marker
              key={k}
              id={`wf-arrow-${k}`}
              markerWidth="7"
              markerHeight="7"
              refX="6"
              refY="3.5"
              orient="auto"
            >
              <path className={cx("wf-arrow", `kind-${k}`)} d="M0,0 L7,3.5 L0,7 z" />
            </marker>
          ))}
        </defs>
        {view.edges.map((e) => {
          const a = posOf.get(e.from);
          const b = posOf.get(e.to);
          if (!a && !b) return null;
          const kind = EDGE_KINDS.includes(e.kind) ? e.kind : "forward";
          // Only the outgoing end gets a stub; the Input chip owns the incoming one.
          if (!a) return null;
          const d = b ? edgeD(a, b) : terminalD(a);
          return (
            <path
              key={`${e.from}>${e.to}`}
              className={cx("edge", `kind-${kind}`, (a.muted || b?.muted) && "muted")}
              d={d}
              markerEnd={`url(#wf-arrow-${kind})`}
            />
          );
        })}
        {interior.map((n) => {
          const isSelected = isSel(n.id);
          const isActive = isLive && activeNode === n.id;
          const { x: cxPos, y: nodeY, muted } = at(n);
          const dotCls = cx(
            "node",
            nodeKind(n.kind).cls,
            isSelected && "selected",
            isActive && "active",
          );
          // Frame = runs a nested pipeline; ring = searched; padlock = openable via `param_keys`
          // but shut (open shackle = partly). Ring and padlock compose; bare dot = never openable.
          const nests = nestAt(n.id);
          const reach = reachByNode?.[n.id] ?? null;
          const lock: "open" | "closed" | null = !reach
            ? null
            : reach.state === "locked"
              ? "closed"
              : reach.state === "partial"
                ? "open"
                : null;
          const reached = reach != null && reach.open > 0;
          const reachNote =
            reach == null || reach.state === "nothing"
              ? null
              : reach.open === 0
                ? `no axis open of ${reach.openable}${reach.held ? " — narrowed at mint" : ""}; open one by forking`
                : `${reach.open} of ${reach.openable} axes open — ${reach.agents.map(agentLabel).join(", ")}`;
          const parts = isSelected || ring ? [n.label] : labelLines(n.label);
          const cellW = ring ? CELL_W : (widths[n.rank] ?? CELL_W);
          const showLabel = !compact && cellW >= 44;
          const labelDy = 16;
          const sub = models
            ? nodeSubLabel(n.kind, models.by[n.id] ?? null, models.loading)
            : "";
          const tip = TERMS[`node_${n.id}`];
          const subDy = labelDy + parts.length * 11;
          const inert = scope == null && nests?.onIsolate == null;
          return (
            <g
              key={n.id}
              className={cx("wf-hero-multi-node", inert && "inert", muted && "muted")}
              transform={`translate(${cxPos} 0)`}
              {...(inert ? {} : pressable(() => activate(n.id)))}
              aria-pressed={inert || nests?.onIsolate ? undefined : isSelected}
              aria-label={
                nests?.onIsolate
                  ? `${n.label} — show what it runs, alone`
                  : reachNote
                    ? `${n.label} — ${reachNote}`
                    : n.label
              }
            >
              <rect
                x={-cellW / 2}
                y={ring ? nodeY - HIT_BAND / 2 : 0}
                width={cellW}
                height={ring ? HIT_BAND : ROW_H}
                fill="transparent"
              />
              <title>
                {[
                  n.label,
                  nests
                    ? nests.onIsolate
                      ? "runs the pipeline below; show it alone"
                      : "runs a whole pipeline of its own"
                    : tip,
                  reachNote,
                ]
                  .filter(Boolean)
                  .join(" — ")}
              </title>
              {/* The node's own glyph is never replaced — reach and lock only adorn it. */}
              {nests ? (
                <g
                  className={cx("node-nest", isActive && "active")}
                  transform={`translate(0 ${nodeY})`}
                >
                  <rect className="frame" x={-8} y={-6} width={16} height={12} rx={2.5} />
                  <rect className="inner" x={-4.5} y={-2.5} width={9} height={5} rx={1.5} />
                </g>
              ) : (
                <circle className={dotCls} cx={0} cy={nodeY} r={RADIUS} />
              )}
              {reached && (
                <circle
                  className={cx("node-reach", isActive && "active")}
                  cx={0}
                  cy={nodeY}
                  r={RADIUS + 3.5}
                />
              )}
              {lock && !compact && (
                <g
                  className={cx("node-lock", `is-${lock}`)}
                  transform={`translate(${RADIUS + 3.4} ${nodeY - RADIUS - 1.5}) scale(0.78)`}
                >
                  <path
                    className="shackle"
                    d={
                      lock === "closed"
                        ? "M-2.6,-1.6 v-2.2 a2.6,2.6 0 0 1 5.2,0 v2.2"
                        : "M-2.6,-1.6 v-2.2 a2.6,2.6 0 0 1 5.2,0"
                    }
                  />
                  <rect className="body" x={-4.6} y={-1.6} width={9.2} height={7.4} rx={1.4} />
                </g>
              )}
              {showLabel && (
                <text className="node-label" x={0} y={nodeY + labelDy} textAnchor="middle">
                  {parts.map((p, j) => (
                    <tspan key={j} x={0} dy={j === 0 ? 0 : 11}>
                      {p}
                    </tspan>
                  ))}
                </text>
              )}
              {showLabel && sub && (
                <text className="node-sub" x={0} y={nodeY + subDy} textAnchor="middle">
                  {sub}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      </div>
    </div>
  );
}

export interface PipelineFlowProps {
  view: PipelineView | null;
  status: PipelineStatus;
  connector: string | null;
  reach: Record<string, NodeReach> | null;
  scope: NodeScope | null;
  nestsNode: string | null;
  activeNode: string | null;
  isLive: boolean;
  leading?: ReactNode;
  // Present on exactly one level of a stack — the only one a sample flows through.
  queryPath?: {
    pressed: boolean;
    label: string;
    onClick: () => void;
    connector: ReactNode;
  };
  // Which levels draw is owned by the STACK, never a `useState` here: a zoom re-parents this
  // flow, and React drops a re-parented component's state.
  nest?: { level: ReactNode; onIsolate: () => void };
  // From the stack: a level cannot know its own depth, and CSS cannot count inside-out.
  tone: "accent" | "neutral";
  models?: { by: Record<string, string | null>; loading: boolean } | null;
  bare?: boolean;
}

function FlowEnd({
  icon,
  lbl,
  val,
  path,
}: {
  icon: ReactNode;
  lbl: string;
  val: string;
  path: NonNullable<PipelineFlowProps["queryPath"]>;
}) {
  return (
    <button
      type="button"
      className="wf-hero-node wf-hero-node-toggle"
      aria-pressed={path.pressed}
      aria-label={path.label}
      onClick={path.onClick}
    >
      <span className="ico">{icon}</span>
      <span className="text-col">
        <span className="lbl">{lbl}</span>
        <span className="val">{val}</span>
      </span>
    </button>
  );
}

export function PipelineFlow({
  view,
  status,
  connector,
  reach,
  scope,
  nestsNode,
  activeNode,
  isLive,
  leading,
  queryPath,
  nest,
  tone,
  models = null,
  bare = false,
}: PipelineFlowProps) {
  const interior = interiorNodes(view);
  const box =
    view == null || interior.length === 0 ? (
      <PipelinePlaceholder status={status === "ok" ? "unbound" : status} />
    ) : (
      <PipelineBox
        view={view}
        connector={connector}
        activeNode={activeNode}
        isLive={isLive}
        reach={reach}
        scope={scope}
        nest={nestsNode ? { node: nestsNode, onIsolate: nest?.onIsolate ?? null } : null}
        compact={nest != null}
        models={models}
      />
    );

  if (bare) return box;

  return (
    <div className="wf-hero-flow">
      {leading}
      {queryPath && (
        <>
          <FlowEnd icon={ATTACH_ICON} lbl="Input" val="Query" path={queryPath} />
          <div className="wf-hero-arrow">{queryPath.connector}</div>
        </>
      )}
      {/* Box and nested level are SIBLINGS: inside the box they would fall under every
          `.wf-hero-node.llm <part>` rule in chat.css. */}
      <div className={cx("wf-hero-unit", `tone-${tone}`, nest && "has-nested")}>
        {box}
        {nest && <div className="wf-hero-nested">{nest.level}</div>}
      </div>
      {queryPath && (
        <>
          <div className="wf-hero-arrow" />
          <FlowEnd icon={ANSWER_ICON} lbl="Output" val="Answer" path={queryPath} />
        </>
      )}
    </div>
  );
}

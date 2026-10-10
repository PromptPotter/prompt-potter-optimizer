"use client";
import type { ReactNode } from "react";
import {
  nodeKind,
  nodeSubLabel,
  type PipelineView,
  type PipelineViewEdge,
  type PipelineViewNode,
} from "@/lib/types";
import type { NodeReach } from "@/lib/api";
import type { NodeScope } from "@/lib/SelectionContext";
import type { PipelineStatus } from "@/lib/types";
import { isMeasuring, useCycleStream } from "@/lib/poll";
import { useSelection } from "@/lib/SelectionContext";
import { MOVABLE_AGENT_LABELS } from "@/lib/api/types.generated";
import {
  cycleOf,
  interiorNodes,
  layoutGrid,
  liveObserveConfig,
} from "@/lib/derivations";
import { Icon, pressable } from "@/components/ui";
import { cx } from "@/lib/cx";

const EDGE_KINDS = Object.keys({
  forward: true,
  loop: true,
  alternative: true,
  directive: true,
} satisfies Record<PipelineViewEdge["kind"], true>) as PipelineViewEdge["kind"][];

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
  // A node absent from the map is UNKNOWN — never draw it as shut.
  reach: Record<string, NodeReach> | null;
  scope: NodeScope | null;
  nestsNode: string | null;
  compact: boolean;
  models: { by: Record<string, string | null>; loading: boolean } | null;
}

function PipelineBox({
  view,
  connector,
  activeNode,
  isLive,
  reach: reachByNode,
  scope,
  nestsNode,
  compact,
  models,
}: BoxProps) {
  const { node: selected, setSelectionForNode: setSelected } = useSelection();
  const { dash } = useCycleStream();
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
  const activate = (id: string) => {
    if (scope == null) return;
    setSelected(isSel(id) ? null : { id, scope });
  };

  const cycle = cycleOf(interior, view.edges);

  // `derive_pipeline_view` serves a loopless view on tier 0, so `rank` alone columns it.
  const cols = Math.max(interior.length, 1);

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

  const edgeD = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const [x1, y1] = [a.x + (dx / len) * RADIUS, a.y + (dy / len) * RADIUS];
    const [x2, y2] = [b.x - (dx / len) * RADIUS, b.y - (dy / len) * RADIUS];
    const mx = (x1 + x2) / 2;

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

  const terminalD = (p: { x: number; y: number }) =>
    `M ${p.x + RADIUS} ${p.y} L ${p.x + CELL_W * 0.5} ${p.y}`;

  const labelLines = (label: string) =>
    label.includes("_") ? label.split("_") : [label];

  // `active_node` names a node here only on a self-optimizing campaign; the chip then holds unpulsed.
  const namedHere = isLive && interior.some((n) => n.id === activeNode);
  const calling = isLive && isMeasuring(dash) && !namedHere;

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
    return (
      <div
        className={cx(
          "wf-hero-node",
          "llm",
          isSelected && "selected",
          isLive && "running",
          calling && "scoring",
        )}
      >
        {connector && <div className="wf-hero-multi-tag">{connector}</div>}
        <button
          type="button"
          className="wf-hero-sole"
          aria-pressed={isSelected}
          aria-label={`Node: ${sole.label}`}
          disabled={scope == null}
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
    <div
      className={cx(
        "wf-hero-node",
        "llm",
        "wf-hero-node-multi",
        isLive && "running",
        calling && "scoring",
      )}
    >
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
          // Only the outgoing end gets a stub; the Input chip owns the incoming one.
          if (!a) return null;
          const d = b ? edgeD(a, b) : terminalD(a);
          return (
            <path
              key={`${e.from}>${e.to}`}
              className={cx("edge", `kind-${e.kind}`, (a.muted || b?.muted) && "muted")}
              d={d}
              markerEnd={`url(#wf-arrow-${e.kind})`}
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
          const nests = n.id === nestsNode;
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
                : `${reach.open} of ${reach.openable} axes open — ${reach.agents.map((a) => MOVABLE_AGENT_LABELS[a]).join(", ")}`;
          const parts = isSelected || ring ? [n.label] : labelLines(n.label);
          const cellW = ring ? CELL_W : (widths[n.rank] ?? CELL_W);
          const showLabel = !compact && cellW >= 44;
          const labelDy = 16;
          const sub = models
            ? nodeSubLabel(n.kind, models.by[n.id] ?? null, models.loading)
            : "";
          const subDy = labelDy + parts.length * 11;
          const inert = scope == null;
          return (
            <g
              key={n.id}
              className={cx("wf-hero-multi-node", inert && "inert", muted && "muted")}
              transform={`translate(${cxPos} 0)`}
              {...(inert ? {} : pressable(() => activate(n.id)))}
              aria-pressed={inert ? undefined : isSelected}
              aria-label={reachNote ? `${n.label} — ${reachNote}` : n.label}
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
                  nests ? "runs a whole pipeline of its own" : n.description,
                  reachNote,
                ]
                  .filter(Boolean)
                  .join(" — ")}
              </title>
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
  inspector?: ReactNode;
  // Owned by the STACK, never a `useState` here: a zoom re-parents this flow and React drops its state.
  nested?: ReactNode;
  tone: "accent" | "neutral";
  models?: { by: Record<string, string | null>; loading: boolean } | null;
  bare?: boolean;
}

function FlowEnd({ icon, lbl, val }: { icon: ReactNode; lbl: string; val: string }) {
  return (
    <div className="wf-hero-node wf-hero-end" aria-label={`${lbl}: ${val}`}>
      <span className="ico">{icon}</span>
      <span className="text-col">
        <span className="lbl">{lbl}</span>
        <span className="val">{val}</span>
      </span>
    </div>
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
  inspector,
  nested,
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
        nestsNode={nestsNode}
        compact={nested != null}
        models={models}
      />
    );

  if (bare) return box;

  return (
    <div className="wf-hero-flow">
      {inspector != null && (
        <>
          <FlowEnd icon={ATTACH_ICON} lbl="Input" val="Query" />
          <div className="wf-hero-arrow">{inspector}</div>
        </>
      )}
      {/* Siblings: inside the box, the nested level matches every `.wf-hero-node.llm <part>` rule in chat.css. */}
      <div className={cx("wf-hero-unit", `tone-${tone}`, nested != null && "has-nested")}>
        {box}
        {nested != null && <div className="wf-hero-nested">{nested}</div>}
      </div>
      {inspector != null && (
        <>
          <div className="wf-hero-arrow" />
          <FlowEnd icon={ANSWER_ICON} lbl="Output" val="Answer" />
        </>
      )}
    </div>
  );
}

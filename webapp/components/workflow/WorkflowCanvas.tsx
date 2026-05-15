"use client";
import { useEffect, useState } from "react";
import { CANVAS_W, CANVAS_H, EDGES, LAYOUT, phaseToNodeId } from "./layout";
import { TERMS } from "@/lib/terms";
import { getCss } from "@/lib/theme";
import { roundOf, type DashboardSnapshot } from "@/lib/poll";
import { useSelection } from "@/components/dashboard/SelectionContext";

export interface PipelineView {
  nodes: { id: string; label: string; kind?: string }[];
  edges: { from: string; to: string }[];
}

export interface PipelineDoc {
  view?: PipelineView;
  nodes?: Record<string, { type?: string; config?: Record<string, unknown>; model?: string }>;
}

export interface NodeDataLike {
  model?: string;
  duration_s?: number;
  round?: number;
  timestamp?: string;
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  input?: { template_name?: string };
  output?: { candidates?: { idx?: number; stats?: Record<string, unknown> }[] };
}

function fmtSecs(s: number | undefined | null): string {
  if (s == null) return "—";
  if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
  if (s < 60) return `${s.toFixed(2)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

// Edge variants — collapses three parallel switches (stroke colour key,
// dasharray, arrowhead marker id) into one row per kind. Add a kind:
// extend the map, no other site changes.
type ColorKey = "txt" | "ok" | "acc" | "esc";
const EDGE_VARIANTS: Record<string, { color: ColorKey; dash: string; marker: string }> = {
  forward:   { color: "txt", dash: "",    marker: "arrh" },
  loop:      { color: "ok",  dash: "5 3", marker: "arrh-loop" },
  directive: { color: "acc", dash: "4 3", marker: "arrh-dir" },
  escalate:  { color: "esc", dash: "2 3", marker: "arrh-esc" },
};

interface Props {
  pipeline: PipelineDoc | null;
  dash: DashboardSnapshot | null;
}

export function WorkflowCanvas({ pipeline, dash }: Props) {
  const view = pipeline?.view;
  const activeId = phaseToNodeId(dash?.state);
  // Node selection rides the shared SelectionContext so SharedInspector
  // (below the row) can render NodePanel details for the picked node.
  // No internal state — clicking ✕ in the inspector clears the context.
  const { node: selected, setNode: setSelected } = useSelection();
  // Bumped by the MutationObserver below on `data-theme` flips; drives the
  // `colors` memo so SVG strokes/labels re-derive from the new CSS vars.
  const [themeTick, setThemeTick] = useState(0);

  useEffect(() => {
    const obs = new MutationObserver(() => setThemeTick((n) => n + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  // Re-derived on every render — `themeTick` bumps cause the re-render,
  // which re-reads CSS vars fresh. No memo: 5 var reads is cheap, and
  // useMemo would need themeTick in its deps even though the body doesn't
  // reference it (tripping react-hooks/exhaustive-deps).
  void themeTick;
  const colors =
    typeof window === "undefined"
      ? { txt: "#9ca3af", ok: "#10b981", acc: "#f59e0b", esc: "#ea580c", bg: "#0d0d0d" }
      : {
          txt: getCss("--color-text-secondary"),
          ok: getCss("--color-success"),
          acc: getCss("--color-accent"),
          esc: getCss("--color-accent-strong"),
          bg: getCss("--color-background-tertiary"),
        };

  if (!view) {
    return (
      <div className={`workflow-card${selected ? " expanded" : " collapsed"}`}>
        <div className="workflow-toolbar">
          <span style={{ flex: 1 }}>Workflow</span>
          <span id="wf-status" style={{ color: "var(--color-text-tertiary)" }}>● topology pending</span>
        </div>
        <div className="workflow-canvas-bg" style={{ minHeight: 200 }} />
      </div>
    );
  }

  const currentNodes = (dash?.current_round?.nodes ?? {}) as Record<string, NodeDataLike>;

  const markerColors: Record<string, string> = {
    arrh: colors.txt,
    "arrh-loop": colors.ok,
    "arrh-dir": colors.acc,
    "arrh-esc": colors.esc,
  };

  return (
    <div className={`workflow-card${selected ? " expanded" : " collapsed"}`}>
      <div className="workflow-toolbar">
        <span style={{ flex: 1 }}>Workflow</span>
        <span style={{ color: dash ? colors.ok : colors.txt }}>● {dash ? "live" : "pending"}</span>
        {(() => {
          const r = roundOf(dash);
          return r != null && r !== 0 ? (
            <span style={{ color: "var(--color-text-tertiary)", marginLeft: 6 }}>round {r}</span>
          ) : null;
        })()}
      </div>
      <div className="workflow-canvas-bg">
        <div className="workflow-canvas">
          <svg
            width="100%"
            height="100%"
            viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
            preserveAspectRatio="xMidYMid meet"
            style={{ position: "absolute", inset: 0 }}
            aria-hidden="true"
          >
            <defs>
              {Object.entries(markerColors).map(([id, fill]) => (
                <marker key={id} id={id} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                  <path d="M0,0 L8,4 L0,8 z" fill={fill} />
                </marker>
              ))}
            </defs>
            {view.edges.map((e) => {
              const geom = EDGES[`${e.from}>${e.to}`];
              if (!geom) return null;
              const v = EDGE_VARIANTS[geom.kind] ?? EDGE_VARIANTS.forward;
              const stroke = colors[v.color];
              return (
                <g key={`${e.from}>${e.to}`}>
                  <path d={geom.d} fill="none" stroke={stroke} strokeWidth="1.5" strokeDasharray={v.dash || undefined} markerEnd={`url(#${v.marker})`} />
                  {geom.label && geom.labelXY && (
                    <text x={geom.labelXY[0]} y={geom.labelXY[1]} fontSize="12" fill={stroke} textAnchor="middle" fontFamily="ui-sans-serif,system-ui" paintOrder="stroke" stroke={colors.bg} strokeWidth="3">{geom.label}</text>
                  )}
                </g>
              );
            })}
            {view.nodes.map((n) => {
              const pos = LAYOUT[n.id];
              if (!pos) return null;
              const data = currentNodes[n.id];
              const hasData = !!data;
              const isActive = activeId === n.id;
              const cls = ["wf-box", `kind-${n.kind || "llm"}`];
              if (isActive) cls.push("active");
              if (!hasData && !isActive && n.kind === "llm") cls.push("dim");
              if (selected === n.id) cls.push("selected");
              const sub = n.kind === "io" ? ""
                : n.kind === "measurement" ? "system step"
                : hasData ? (data?.model || "—") : "idle";
              const tip = TERMS[`node_${n.id}`] || "";
              return (
                <foreignObject key={n.id} x={pos.x} y={pos.y} width={pos.w} height={pos.h} style={{ overflow: "visible" }}>
                  <div style={{ width: "100%", height: "100%" }}>
                    <button
                      type="button"
                      className="wf-node"
                      aria-label={n.kind === "io" ? `${n.label} (I/O)` : `Node: ${n.label}`}
                      aria-pressed={selected === n.id ? "true" : undefined}
                      tabIndex={n.kind === "io" ? -1 : 0}
                      disabled={n.kind === "io"}
                      onClick={() => n.kind !== "io" && setSelected(n.id)}
                      title={tip || undefined}
                    >
                      <div className={cls.join(" ")}>
                        <div className="wf-node-title">{n.label}</div>
                        {sub && <div className="wf-node-sub">{sub}</div>}
                      </div>
                    </button>
                  </div>
                </foreignObject>
              );
            })}
          </svg>
        </div>
      </div>
    </div>
  );
}

interface PanelProps {
  id: string;
  view: PipelineView;
  currentNodes: Record<string, NodeDataLike>;
  pipeline: PipelineDoc | null;
  phase: string | undefined;
  round: number | undefined;
  onClose: () => void;
}

export function NodePanel({ id, view, currentNodes, pipeline, phase, round, onClose }: PanelProps) {
  const meta = view.nodes.find((n) => n.id === id);
  const data = currentNodes[id];
  const cfg = pipeline?.nodes?.[id];

  let rows: [string, string][];
  if (data) {
    const tokens = data.usage
      ? `${data.usage.prompt_tokens ?? "—"}p / ${data.usage.completion_tokens ?? "—"}c / ${data.usage.total_tokens ?? "—"}t`
      : "—";
    rows = [
      ["status",    "live · this round"],
      ["template",  data.input?.template_name || "—"],
      ["model",     data.model || "—"],
      ["duration",  fmtSecs(data.duration_s)],
      ["tokens",    tokens],
      ["round",     String(data.round ?? "—")],
      ["timestamp", data.timestamp || "—"],
    ];
  } else if (meta?.kind === "measurement") {
    rows = [
      ["kind",  "system step"],
      ["role",  "measure_sample (no LLM call)"],
      ["phase", String(phase ?? "—")],
    ];
  } else if (meta?.kind === "phase") {
    const isCurrent = phase === id || (id === "origin" && phase === "origin");
    rows = [
      ["kind",   "phase / system step"],
      ["role",   id === "origin" ? "measure seed candidate (round 0 only)" : "—"],
      ["status", isCurrent ? `active · ${phase}` : `idle (currently ${phase ?? "—"})`],
    ];
  } else if (meta?.kind === "io") {
    rows = [
      ["kind", "I/O"],
      ["note", id === "input" ? "dataset feed-in" : "best-SP exit"],
    ];
  } else if (cfg) {
    const c = (cfg.config ?? {}) as Record<string, unknown>;
    rows = [
      ["status",          phase === "origin" ? "idle · origin phase only fires l1_score" : `idle · has not fired in round ${round ?? "—"}`],
      ["type",            (cfg.type as string) ?? "—"],
      ["temperature",     String(c.temperature ?? "—")],
      ["output_format",   String(c.output_format ?? "—")],
      ["prompt_family",   String(c.prompt_family ?? "—")],
      ["schema_family",   String(c.schema_family ?? "—")],
      ["response_parser", String(c.response_parser ?? "—")],
    ];
  } else {
    rows = [["status", "no data this round"]];
  }

  return (
    <div className="sel-panel" role="region" aria-label={`Node detail: ${meta?.label ?? id}`}>
      <button type="button" className="sel-close" onClick={onClose} aria-label="Close node detail">×</button>
      <div className="sel-panel-title">{meta?.label || id}</div>
      {rows.map(([k, v]) => (
        <div key={k} className="sel-row">
          <span>{k}</span>
          <span className="sel-val">{v}</span>
        </div>
      ))}
    </div>
  );
}

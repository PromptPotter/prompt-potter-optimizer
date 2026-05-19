"use client";
import { useMemo, useState } from "react";
import type { PipelineDoc } from "./types";
import { type DashboardSnapshot, roundOf, type RoundFileDoc } from "@/lib/poll";
import { phaseToNodeId } from "./layout";

// Node block as written by AuditTrailView._handle_llm_call
// (promptpotter/infrastructure/projections/audit_trail.py:222-243).
// Both dashboard.json::current_round.nodes and round_NNNN.json::nodes share
// this shape — input/output are loose dicts whose contents vary by node.
interface NodeBlock {
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  model?: string;
  duration_s?: number;
  timestamp?: string;
  round?: number;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
}

interface Props {
  id: string;
  pipeline: PipelineDoc | null;
  dash: DashboardSnapshot | null;
  rounds: RoundFileDoc[];
  onClose: () => void;
}

function fmtSecs(s: number | undefined | null): string {
  if (s == null) return "—";
  if (s < 1) return `${(s * 1000).toFixed(0)}ms`;
  if (s < 60) return `${s.toFixed(2)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

function fmtVal(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function fmtInline(v: unknown): string {
  if (v == null || v === "") return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
    return String(v);
  }
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

interface RoundOption {
  round: number;
  block: NodeBlock;
  live: boolean;
}

function collectRounds(
  id: string,
  dash: DashboardSnapshot | null,
  rounds: RoundFileDoc[],
): RoundOption[] {
  const out: RoundOption[] = [];
  const seen = new Set<number>();
  const liveRound = roundOf(dash);
  const liveNodes = dash?.current_round?.nodes as
    | Record<string, NodeBlock>
    | undefined;
  const liveBlock = liveNodes?.[id];
  if (liveRound != null && liveBlock && typeof liveBlock === "object") {
    out.push({ round: liveRound, block: liveBlock, live: true });
    seen.add(liveRound);
  }
  for (const r of rounds) {
    const rn = typeof r.round === "number" ? r.round : null;
    if (rn == null || seen.has(rn)) continue;
    const nodes = r.nodes as Record<string, NodeBlock> | undefined;
    const block = nodes?.[id];
    if (block && typeof block === "object") {
      out.push({ round: rn, block, live: false });
      seen.add(rn);
    }
  }
  return out.sort((a, b) => b.round - a.round);
}

// One-line "chip" inside the meta strip: small label + value, separated by a
// thin divider. Skipped entirely when the value is empty so the strip stays
// dense.
function Chip({ label, value }: { label: string; value: string }) {
  if (!value || value === "—") return null;
  return (
    <span className="opt-detail-chip">
      <span className="opt-detail-chip-label">{label}</span>
      <span className="opt-detail-chip-value">{value}</span>
    </span>
  );
}

export function OptimizerNodeDetail({ id, pipeline, dash, rounds, onClose }: Props) {
  const view = pipeline?.view;
  const meta = view?.nodes.find((n) => n.id === id);
  const label = meta?.label ?? id;
  const kind = meta?.kind ?? "llm";

  const cfg = pipeline?.nodes?.[id];
  const cfgInner = (cfg?.config ?? {}) as Record<string, unknown>;

  const options = useMemo(() => collectRounds(id, dash, rounds), [id, dash, rounds]);
  const [pickedRound, setPickedRound] = useState<number | null>(null);
  const active =
    options.find((o) => o.round === pickedRound) ?? options[0] ?? null;

  const block = active?.block ?? null;
  const templateFields = block?.input?.template_fields as
    | Record<string, unknown>
    | undefined;
  const templateName = (block?.input?.template_name as string | undefined) ?? null;
  const response = block?.output?.response;
  const otherOutput = block?.output
    ? Object.fromEntries(
        Object.entries(block.output).filter(([k]) => k !== "response"),
      )
    : {};
  const usage = block?.usage;
  const tokens = usage
    ? `${usage.prompt_tokens ?? "—"}p / ${usage.completion_tokens ?? "—"}c / ${usage.total_tokens ?? "—"}t`
    : "";

  const livePhaseNode = phaseToNodeId(dash?.state ?? null);
  const isLiveNow = livePhaseNode === id;
  const lastFiredRound = options[0]?.round ?? null;
  const statusLine = isLiveNow
    ? `live · round ${roundOf(dash) ?? "—"}`
    : lastFiredRound != null
      ? `last fired round ${lastFiredRound}`
      : kind === "llm"
        ? "not yet fired"
        : kind;

  const kindBadge =
    kind === "llm" ? "LLM"
    : kind === "measurement" ? "system step"
    : kind === "phase" ? "phase"
    : kind === "io" ? "I/O"
    : kind;

  const hasConfig =
    !!cfg && (cfg.type != null || Object.keys(cfgInner).length > 0);

  return (
    <div className="opt-detail">
      <header className="opt-detail-head">
        <div className="opt-detail-head-titles">
          <h2 className="opt-detail-name">{label}</h2>
          <code className="opt-detail-id">{id}</code>
          <span className={`opt-detail-kind kind-${kind}`}>{kindBadge}</span>
          <span className={`opt-detail-status${isLiveNow ? " live" : ""}`}>
            ● {statusLine}
          </span>
        </div>
        <div className="opt-detail-head-actions">
          {options.length > 1 && (
            <label className="opt-detail-round-pick">
              round
              <select
                value={active?.round ?? ""}
                onChange={(e) => setPickedRound(Number(e.target.value))}
                aria-label="Choose round"
              >
                {options.map((o) => (
                  <option key={o.round} value={o.round}>
                    {o.round}
                    {o.live ? " (live)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            className="opt-detail-close"
            onClick={onClose}
            aria-label="Close detail"
            title="Close"
          >
            ×
          </button>
        </div>
      </header>

      <div className="opt-detail-meta">
        <Chip label="model" value={block?.model ?? ""} />
        <Chip label="dur" value={block ? fmtSecs(block.duration_s) : ""} />
        <Chip label="tokens" value={tokens} />
        <Chip label="template" value={templateName ?? ""} />
        <Chip label="type" value={(cfg?.type as string) ?? ""} />
        <Chip label="temp" value={fmtInline(cfgInner.temperature)} />
        <Chip label="format" value={fmtInline(cfgInner.output_format)} />
        <Chip label="prompt" value={fmtInline(cfgInner.prompt_family)} />
        <Chip label="schema" value={fmtInline(cfgInner.schema_family)} />
        <Chip label="parser" value={fmtInline(cfgInner.response_parser)} />
        <Chip label="ts" value={block?.timestamp ?? ""} />
      </div>

      {!block ? (
        <div className="opt-detail-empty">
          {kind === "measurement"
            ? "System step — no LLM call. See the configuration footer for the wired params."
            : kind === "phase"
              ? "Phase marker — no LLM call."
              : "This node has not fired in any cached round yet."}
        </div>
      ) : (
        <div className="opt-detail-cols">
          <section className="opt-detail-col opt-detail-col-fields" aria-label="Template fields">
            <div className="opt-detail-col-head">
              <span>Template fields</span>
              {templateFields && (
                <span className="opt-detail-col-count">
                  {Object.keys(templateFields).length}
                </span>
              )}
            </div>
            <div className="opt-detail-col-body">
              {templateFields && Object.keys(templateFields).length > 0 ? (
                <dl className="opt-detail-fields">
                  {Object.entries(templateFields).map(([k, v]) => (
                    <div key={k} className="opt-detail-field">
                      <dt>{k}</dt>
                      <dd>
                        <pre>{fmtVal(v)}</pre>
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <div className="opt-detail-col-empty">No template fields on this block.</div>
              )}
            </div>
          </section>

          <section className="opt-detail-col opt-detail-col-response" aria-label="Response">
            <div className="opt-detail-col-head">
              <span>Response</span>
            </div>
            <div className="opt-detail-col-body">
              {response != null ? (
                <pre className="opt-detail-pre">{fmtVal(response)}</pre>
              ) : isLiveNow ? (
                <div className="opt-detail-col-empty">In flight — response not yet written.</div>
              ) : Object.keys(otherOutput).length > 0 ? (
                <pre className="opt-detail-pre">{fmtVal(otherOutput)}</pre>
              ) : (
                <div className="opt-detail-col-empty">No response on this block.</div>
              )}
            </div>
          </section>
        </div>
      )}

      <footer className="opt-detail-footer">
        {block && (
          <details className="opt-detail-disclosure">
            <summary>raw input</summary>
            <pre className="opt-detail-pre">{fmtVal(block.input ?? {})}</pre>
          </details>
        )}
        {block && (
          <details className="opt-detail-disclosure">
            <summary>raw output</summary>
            <pre className="opt-detail-pre">{fmtVal(block.output ?? {})}</pre>
          </details>
        )}
        {hasConfig && (
          <details className="opt-detail-disclosure">
            <summary>configuration</summary>
            <pre className="opt-detail-pre">
              {fmtVal({ type: cfg?.type, ...cfgInner })}
            </pre>
          </details>
        )}
      </footer>
    </div>
  );
}

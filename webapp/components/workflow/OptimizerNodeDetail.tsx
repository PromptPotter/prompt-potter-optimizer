"use client";
import { useMemo, useState } from "react";
import type { PipelineDoc } from "./types";
import { type DashboardSnapshot, roundOf } from "@/lib/poll";
import { useRoundFile } from "@/lib/useRoundFile";
import { phaseToNodeId } from "./layout";
import { fmtSecs } from "@/lib/format";
import type { NodeBlock } from "@/lib/types/round";

interface Props {
  id: string;
  pipeline: PipelineDoc | null;
  dash: DashboardSnapshot | null;
  // Freshness gate — a frozen cycle keeps `dash.state` at its last phase;
  // without this the detail panel would claim the node is "live" forever.
  isLive: boolean;
  // Needed for the lazy per-round fetch when the operator picks a
  // historical round from the dropdown.
  campaignId: string | null;
  cycleId: string | null;
  onClose: () => void;
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

// Resolve a node block for a given round:
// - live round: read inline from `dash.current_round.nodes[id]`
// - historical: read from the lazily-fetched round file's `nodes[id]`
// `null` when the node did not fire in that round (or the file hasn't
// landed yet).
function liveNodeBlock(
  dash: DashboardSnapshot | null,
  id: string,
): NodeBlock | null {
  const liveNodes = dash?.current_round?.nodes as Record<string, NodeBlock> | undefined;
  const block = liveNodes?.[id];
  return block && typeof block === "object" ? block : null;
}

export function OptimizerNodeDetail({
  id,
  pipeline,
  dash,
  isLive,
  campaignId,
  cycleId,
  onClose,
}: Props) {
  const view = pipeline?.view;
  const meta = view?.nodes.find((n) => n.id === id);
  const label = meta?.label ?? id;
  const kind = meta?.kind ?? "llm";

  const cfg = pipeline?.nodes?.[id];
  const cfgInner = (cfg?.config ?? {}) as Record<string, unknown>;

  // Available rounds in the picker, newest first. The live round (if any)
  // sits at the top with a "(live)" label. Completed rounds come from
  // `dash.rounds[]` — the summary surface owns "which rounds exist on disk."
  const liveRound = roundOf(dash);
  const completedRounds = useMemo(() => {
    const out = (dash?.rounds ?? []).map((r) => r.round);
    return out.sort((a, b) => b - a);
  }, [dash?.rounds]);
  const pickerOptions = useMemo(() => {
    const seen = new Set<number>();
    const out: { round: number; live: boolean }[] = [];
    if (liveRound != null) {
      out.push({ round: liveRound, live: true });
      seen.add(liveRound);
    }
    for (const r of completedRounds) {
      if (seen.has(r)) continue;
      out.push({ round: r, live: false });
      seen.add(r);
    }
    return out;
  }, [liveRound, completedRounds]);

  const [pickedRound, setPickedRound] = useState<number | null>(null);
  const activeRound =
    pickedRound != null ? pickedRound : (pickerOptions[0]?.round ?? null);
  const activeIsLive = activeRound != null && activeRound === liveRound;

  // Live round: read inline (no fetch). Historical round: lazy-fetch the
  // round file; `block` is null until it lands.
  const liveBlock = liveNodeBlock(dash, id);
  const historicalRound = activeIsLive ? null : activeRound;
  const { doc: historicalDoc } = useRoundFile(campaignId, cycleId, historicalRound);
  const block: NodeBlock | null = useMemo(() => {
    if (activeRound == null) return null;
    if (activeIsLive) return liveBlock;
    const nodes = historicalDoc?.nodes as Record<string, NodeBlock> | undefined;
    const b = nodes?.[id];
    return b && typeof b === "object" ? b : null;
  }, [activeRound, activeIsLive, liveBlock, historicalDoc, id]);
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
  const isLiveNow = isLive && livePhaseNode === id;
  const lastFiredRound = pickerOptions.find((o) => !o.live)?.round ?? null;
  const statusLine = isLiveNow
    ? `live · round ${liveRound ?? "—"}`
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
          {pickerOptions.length > 1 && (
            <label className="opt-detail-round-pick">
              round
              <select
                value={activeRound ?? ""}
                onChange={(e) => setPickedRound(Number(e.target.value))}
                aria-label="Choose round"
              >
                {pickerOptions.map((o) => (
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

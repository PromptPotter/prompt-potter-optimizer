"use client";
import { nodeKindLabel, type PipelineDoc } from "./types";
import { RoundSamplesView } from "@/components/dashboard/samples/RoundSamplesView";
import { availableRounds, isWidgetParam } from "@/lib/derivations";
import { useRoundNodes } from "@/lib/hooks/useRoundNodes";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { activeNodeId } from "./layout";
import { fmtSecs, fmtValue } from "@/lib/format";
import { CopyButton } from "@/components/ui";
import { NodeConfigEditor } from "@/components/dashboard/control/NodeConfigEditor";
import type { NodeBlock } from "@/lib/types";

interface Props {
  id: string;
  pipeline: PipelineDoc | null;
  onClose: () => void;
}

export function OptimizerNodeDetail({ id, pipeline, onClose }: Props) {
  // Self-sourced: live snapshot + liveness from the cycle stream. `isLive` is the
  // freshness gate — a frozen cycle keeps `dash.state` at its last phase, so
  // without it the panel would claim the node is "live" forever.
  const { dash, isLive, dashRound: liveRound } = useDashboard();
  const view = pipeline?.view;
  const meta = view?.nodes.find((n) => n.id === id);
  const label = meta?.label ?? id;
  const kind = meta?.kind ?? "llm";

  const cfg = pipeline?.nodes?.[id];
  const cfgInner = (cfg?.config ?? {}) as Record<string, unknown>;
  // The node's typed config surface (model / provider / reasoning_effort / temp / …),
  // served by `/optimizer-pipeline`. Rendered read-only through the SAME config element
  // the steer panel uses, so the operator sees every knob the backend exposes instead of
  // a hand-rolled chip strip. Empty for phase / measurement nodes (no config to show).
  const configSchema = pipeline?.node_config_schema ?? null;
  // Widget-kind only, so the gate + count below agree with what `NodeConfigEditor`
  // actually draws — the served list is complete and also carries prompt/nested
  // params, which no widget renders.
  const configParams = (configSchema?.[id] ?? []).filter(isWidgetParam);

  // The round's node blocks come from the one resolver this card shares with the
  // canvas above it (`useRoundNodes`) — same round, same source-selection rule.
  // Splitting that switch across the two surfaces is what let them disagree.
  const { nodes, round: activeRound, isLiveRound: activeIsLive } = useRoundNodes();
  // Completed rounds are owned by `availableRounds` (round-axis) — ascending,
  // so the most recent fired round is the tail. Used only for the status line.
  const completed = availableRounds(dash, isLive).completed;
  const lastFiredRound = completed.at(-1) ?? null;

  const block: NodeBlock | null = nodes[id] ?? null;
  const templateFields = block?.input?.template_fields as
    | Record<string, unknown>
    | undefined;
  const templateName = (block?.input?.template_name as string | undefined) ?? null;
  const response = block?.output?.response;
  // The model's own thinking channel, when the provider returned one. Rendered in
  // its own pane rather than folded into the response blob: it is prose a human
  // reads to understand HOW an answer was reached, and it is analytical only —
  // nothing in the loop scores or gates on it (see `LLMResponse.reasoning`).
  const reasoning = typeof block?.output?.reasoning === "string" ? block.output.reasoning : null;
  const otherOutput = block?.output
    ? Object.fromEntries(
        Object.entries(block.output).filter(([k]) => k !== "response" && k !== "reasoning"),
      )
    : {};
  const usage = block?.usage;
  const tokens = usage
    ? `${usage.prompt_tokens ?? "—"}p / ${usage.completion_tokens ?? "—"}c / ${usage.total_tokens ?? "—"}t`
    : "";

  const livePhaseNode = activeNodeId(dash?.in_flight?.node ?? null, dash?.state ?? null);
  // `activeIsLive` too: a node inspected on a historical round is not live, even
  // when that same node happens to be firing in the round currently running.
  const isLiveNow = isLive && activeIsLive && livePhaseNode === id;
  const statusLine = isLiveNow
    ? `live · round ${liveRound ?? "—"}`
    : lastFiredRound != null
      ? `last fired round ${lastFiredRound}`
      : kind === "llm"
        ? "not yet fired"
        : kind;

  const kindBadge = nodeKindLabel(kind);

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
          <CopyButton
            data={block ?? { id, label, kind, config: cfgInner }}
            title="Copy this node's full I/O as JSON"
          />
          {activeRound != null && (
            <span
              className="opt-detail-round-tag"
              title="Round is set by the round axis in the Optimizer toolbar"
            >
              round {activeRound}
              {activeIsLive ? " · live" : ""}
            </span>
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

      {/* Meta strip = RUN facts only (what this node DID this round). Its static
          config — model / provider / temp / format / parser / schema — is owned by the
          Configuration panel below, rendered through the canonical config element, so it
          no longer duplicates here. */}
      <div className="opt-detail-meta">
        <Chip label="model" value={block?.model ?? ""} />
        <Chip label="dur" value={block ? fmtSecs(block.duration_s) : ""} />
        <Chip label="tokens" value={tokens} />
        <Chip label="template" value={templateName ?? ""} />
        <Chip label="type" value={(cfg?.type as string) ?? ""} />
        <Chip label="ts" value={block?.timestamp ?? ""} />
      </div>

      {configParams.length > 0 && (
        <section className="opt-detail-config" aria-label="Node configuration">
          <div className="opt-detail-col-head">
            <span>Configuration</span>
            <span className="opt-detail-col-count">{configParams.length}</span>
          </div>
          <NodeConfigEditor
            mode="values"
            schema={configSchema}
            node={id}
            seedOverlay={{}}
            readOnly
          />
        </section>
      )}

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
                        <pre>{fmtValue(v, { pretty: true })}</pre>
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
                <pre className="opt-detail-pre">{fmtValue(response, { pretty: true })}</pre>
              ) : isLiveNow ? (
                <div className="opt-detail-col-empty">In flight — response not yet written.</div>
              ) : Object.keys(otherOutput).length > 0 ? (
                <pre className="opt-detail-pre">{fmtValue(otherOutput, { pretty: true })}</pre>
              ) : (
                <div className="opt-detail-col-empty">No response on this block.</div>
              )}
            </div>
          </section>

          {reasoning && (
            <section
              className="opt-detail-col opt-detail-col-reasoning"
              aria-label="Model thinking"
            >
              <div className="opt-detail-col-head">
                <span>Thinking</span>
                <span
                  className="opt-detail-col-note"
                  title="The model's own reasoning, recorded for analysis. It never feeds the optimizer's decisions — no score, gate or selection reads it."
                >
                  analysis only
                </span>
              </div>
              <div className="opt-detail-col-body">
                <pre className="opt-detail-pre opt-detail-reasoning">{reasoning}</pre>
              </div>
            </section>
          )}
        </div>
      )}

      {id === "l1_score" && (
        <section className="opt-detail-samples" aria-label="Round samples">
          <RoundSamplesView />
        </section>
      )}

      <footer className="opt-detail-footer">
        {block && (
          <details className="opt-detail-disclosure">
            <summary>raw input</summary>
            <pre className="opt-detail-pre">{fmtValue(block.input ?? {}, { pretty: true })}</pre>
          </details>
        )}
        {block && (
          <details className="opt-detail-disclosure">
            <summary>raw output</summary>
            <pre className="opt-detail-pre">{fmtValue(block.output ?? {}, { pretty: true })}</pre>
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

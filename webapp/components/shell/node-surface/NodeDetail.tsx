"use client";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import type { SelectedNode } from "@/lib/SelectionContext";
import { cx } from "@/lib/cx";
import type { NodeBlock } from "@/lib/types";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useObserveSearchPoint } from "@/lib/hooks/useObserveSearchPoint";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useRoundNodes } from "@/lib/hooks/useRoundNodes";
import {
  cacheShare,
  interiorNodes,
  nodeOriginPrompt,
  observeOptions,
  prefixReading,
  type ObserveState,
} from "@/lib/derivations";
import { fmtPct0, fmtSecs, fmtValue } from "@/lib/format";
import { nodeKind } from "@/components/workflow";
import { CopyButton, SegmentedControl } from "@/components/ui";
import { NodeSurface } from "./NodeSurface";
import { MeasurementRun } from "./MeasurementRun";
import { L1Variants, variantsOf } from "./L1Variants";

// THE node detail — one panel, both canvases, every tab.
//
// It used to be two components that could not see each other: `OptimizerNodeDetail`
// (mounted on Dashboard, rendering the round's LLM I/O) and `BackendNodeDetail`
// (mounted on Chat, rendering the searchpoint's config + prompt). `SelectedNode.scope`
// was doing two jobs at once — disambiguating the id namespace AND choosing which of
// the two rendered — so a scope's detail was reachable only from the tab that happened
// to mount it. Clicking the optimizer rail on the chat hero lit the node and opened
// nothing at all, because its only renderer lived on the other tab.
//
// Scope keeps the first job and loses the second. What a node shows is two halves:
//
//   PROGRAM — what it IS: resolved config, the prompt it runs, its output contract.
//   RUN     — what it DID in the viewed round: rendered input, response, thinking,
//             cost. Optimizer-scoped only, and that is a boundary rather than an
//             omission: the audit twin records the OPTIMIZER's calls, and on a
//             self-optimizing campaign the target pipeline declares the same node
//             ids, so keying a target node into it would show one layer's trace under
//             another layer's heading — the exact confusion this panel was merged to
//             end.
//
// The round is READ here, never set: its control belongs beside the picture it scopes
// (the Optimizer card's toolbar on Dashboard, the hero frame on Chat), so this panel
// showing a second one would be two controls for one axis.

interface Props {
  node: SelectedNode;
  // The active draft while a campaign is being set up; null otherwise. Target scope
  // only — the optimizer's own pipeline is one operator-owned file with no draft.
  draft: DraftCampaignWire | null;
  onClose: () => void;
  // Setup only: makes the target LLM node's prompt editable (persists via this patch).
  onPromptApply?: (patch: DraftPatch) => void;
}

export function NodeDetail({ node: selected, draft, onClose, onPromptApply }: Props) {
  const { id, scope } = selected;
  const isOptimizer = scope === "optimizer";

  const cv = useConnector();
  const { dash, isLive, dashRound: liveRound } = useDashboard();
  // Both gated by argument rather than by an early return — a hook may not be
  // conditional, and the parked side must not spend its round-trip either.
  const { doc: optimizer, loading: pipelineLoading } = useOptimizerPipeline(isOptimizer);
  const observe = useObserveSearchPoint(id, !isOptimizer && !draft);

  const view = isOptimizer ? optimizer?.view : cv.view;
  const node = interiorNodes(view).find((n) => n.id === id) ?? null;
  // `null` where the served view has not resolved this node — still fetching, or absent from the
  // manifest. Deliberately NOT defaulted: the run half below DISPATCHES on this, and a default
  // would render an LLM call's trace for a system step during every fetch. The header is a
  // caption rather than a decision, so it takes `nodeKind`'s own `tool` fallback — the producer's
  // (`pipeline_parsing.py::_derive_node_kind`), spelled in one place.
  const servedKind = node?.kind ?? null;
  const kindInfo = nodeKind(servedKind ?? undefined);
  const schema = isOptimizer ? (optimizer?.node_config_schema ?? null) : cv.nodeConfigSchema;
  const outputSchema = isOptimizer
    ? (optimizer?.node_output_schema ?? null)
    : cv.nodeOutputSchema;

  const {
    nodes: roundNodes,
    round: viewedRound,
    showsCurrent: viewingLive,
    loading: nodesLoading,
  } = useRoundNodes();
  const block: NodeBlock | null = isOptimizer ? (roundNodes[id] ?? null) : null;

  const livePhaseNode = dash?.current_round.active_node ?? null;
  // `viewingLive` too: a node inspected on a historical round is not live, even when
  // that same node happens to be firing in the round currently running.
  const isLiveNow = isLive && viewingLive && livePhaseNode === id && isOptimizer;

  return (
    // The run trace is two columns of payload and needs the band; the program-only
    // panel is a form and reads better in the hero's own column width.
    <div className={cx("bnode", isOptimizer && "bnode-wide")}>
      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">
            <span className={cx("bnode-kind", kindInfo.cls)}>{kindInfo.label}</span>
            {node?.label ?? id}
            <code className="opt-detail-id">{id}</code>
          </span>
          <span className="setup-preview-side">
            <span className={cx("opt-detail-status", isLiveNow && "live")}>
              ● {isLiveNow ? `live · round ${liveRound ?? "—"}` : scopeLabel(isOptimizer)}
            </span>
            {isOptimizer && viewedRound != null && (
              <span
                className="opt-detail-round-tag"
                title="Round is set by the round axis beside the pipeline picture"
              >
                round {viewedRound}
                {viewingLive ? " · live" : ""}
              </span>
            )}
            <CopyButton
              data={block ?? { id, scope, label: node?.label ?? id, kind: servedKind }}
              title="Copy this node's full I/O as JSON"
            />
            <button
              type="button"
              className="bnode-close"
              onClick={onClose}
              aria-label="Close detail"
              title="Close"
            >
              ×
            </button>
          </span>
        </header>

        <p className="bnode-role">{kindInfo.role}</p>

        {isOptimizer ? (
          <OptimizerProgram
            node={node}
            id={id}
            schema={schema}
            outputSchema={outputSchema}
            doc={optimizer}
          />
        ) : (
          <TargetProgram
            node={node}
            draft={draft}
            observe={observe}
            schema={schema}
            outputSchema={outputSchema}
            isLive={isLive}
            onPromptApply={onPromptApply}
          />
        )}

        {/* `viewedRound == null` is NO CAMPAIGN — the setup surface, where nothing has
            run yet. Distinct from a resolved round this node is absent from, which is
            "it did not fire THIS round"; printing that during setup accused a node of
            never having fired when there was no run to fire in. */}
        {isOptimizer && viewedRound != null && (
          <RunSection
            kind={servedKind}
            block={block}
            round={viewedRound}
            loading={nodesLoading}
            kindLoading={pipelineLoading}
            inFlight={isLiveNow}
          />
        )}
      </section>
    </div>
  );
}

function scopeLabel(isOptimizer: boolean): string {
  return isOptimizer ? "the optimizer's own loop" : "this campaign's pipeline";
}

// PROGRAM, optimizer scope. The manifest is one operator-owned file — no draft, no
// per-campaign overlay — so config and prompt are read-only by construction rather
// than by a flag someone could flip.
function OptimizerProgram({
  node,
  id,
  schema,
  outputSchema,
  doc,
}: {
  node: Parameters<typeof NodeSurface>[0]["node"];
  id: string;
  schema: Parameters<typeof NodeSurface>[0]["schema"];
  outputSchema: Parameters<typeof NodeSurface>[0]["outputSchema"];
  doc: Parameters<typeof nodeOriginPrompt>[0];
}) {
  const origin = nodeOriginPrompt(doc, id);
  return (
    <>
      <NodeSurface
        node={node}
        point={{ origin_prompt_fields: origin?.fields ?? {}, pipeline_overlay: {} }}
        configSeed={{}}
        schema={schema}
        outputSchema={outputSchema}
        mode="values"
      />
      {origin && origin.count > 1 && (
        <p className="inspector-note">
          Showing prompt {origin.version} of {origin.count} this node declares.
        </p>
      )}
    </>
  );
}

// PROGRAM, target scope. Dispatches on LIFECYCLE, not on node-presence: authoring a
// draft's search space, previewing a draft whole, or observing what a measured
// searchpoint actually ran. The best / most-recent / selected picker sits above the
// surface — it picks WHICH searchpoint the box shows; the box renders exactly one.
function TargetProgram({
  node,
  draft,
  observe,
  schema,
  outputSchema,
  isLive,
  onPromptApply,
}: {
  node: Parameters<typeof NodeSurface>[0]["node"];
  draft: DraftCampaignWire | null;
  observe: ReturnType<typeof useObserveSearchPoint>;
  schema: Parameters<typeof NodeSurface>[0]["schema"];
  outputSchema: Parameters<typeof NodeSurface>[0]["outputSchema"];
  isLive: boolean;
  onPromptApply?: (patch: DraftPatch) => void;
}) {
  // Two draft lifecycles, one call: AUTHORING a concrete node opens the search-space
  // lock/allow editor over the draft overlay; the draft WHOLE is the origin being authored,
  // and no server-resolved config exists pre-mint, so it gets no callback — which IS
  // read-only.
  if (draft) {
    const authoring = node != null;
    return (
      <NodeSurface
        node={authoring ? node : null}
        point={{ origin_prompt_fields: draft.origin_prompt_fields, pipeline_overlay: {} }}
        configSeed={authoring ? draft.pipeline_overlay : {}}
        schema={schema}
        outputSchema={outputSchema}
        mode={authoring ? "search-space" : "values"}
        onApply={authoring ? onPromptApply : undefined}
      />
    );
  }

  // A one-option group is not a choice: `NodeSurface` renders the `label` naming which
  // searchpoint is on screen, so the group adds nothing until there are two to pick from.
  const options = observeOptions(observe.avail);

  return (
    <>
      {options.length > 1 && (
        <div className="observe-toggle">
          <span className="observe-toggle-label">Searchpoint</span>
          <SegmentedControl<ObserveState>
            options={options}
            value={observe.state}
            onChange={observe.setPref}
            ariaLabel="Which searchpoint to show"
          />
        </div>
      )}
      {observe.cfg ? (
        <>
          <NodeSurface
            node={node}
            point={{ origin_prompt_fields: observe.cfg.promptFields, pipeline_overlay: {} }}
            configSeed={observe.cfg.config}
            schema={schema}
            outputSchema={outputSchema}
            label={observe.cfg.label}
            mode="values"
          />
          {/* A per-node optimizer prompt is carried as the optimizer's evolved DELTA,
              so a node it has not touched yet has nothing here. Six empty boxes under
              a heading reading "Starting prompt" claimed the node runs on nothing; say
              which it is instead. */}
          {Object.keys(observe.cfg.promptFields).length === 0 && (
            <p className="inspector-note">
              The optimizer has not changed this node&apos;s prompt — it still runs the
              one its dataset shipped.
            </p>
          )}
        </>
      ) : (
        // Three different absences, each said differently. A round file still in flight
        // is NOT "nothing measured", and on a fresh campaign the empty state is the
        // whole of round 0 — hours on an L4 run.
        <p className="inspector-note">
          {observe.loading
            ? "Loading the searchpoint…"
            : isLive
              ? "Scoring in progress — the resolved spec appears when the round closes."
              : "No measured searchpoint yet."}
        </p>
      )}
    </>
  );
}

// RUN: what this node did in the viewed round, out of the audit twin.
//
// WHICH account that is depends on what the node IS. A measurement node runs a whole pipeline
// rather than a prompt: it has no rendered input, no response and no token bill, so offering
// those three for one describes a call it never makes — which is what this panel used to do,
// printing "No template fields on this block" and "No response on this block" for keys the
// block never carried, and then bolting the real content on behind a hard-coded node id.
//
// Dispatch is on the served `kind` and nothing else. Block SHAPE answers the same question a
// second way and gets it wrong: an LLM node that has not fired yet is equally missing all three
// keys, and would take the measurement arm.
function RunSection({
  kind,
  block,
  round,
  loading,
  kindLoading,
  inFlight,
}: {
  kind: string | null;
  block: NodeBlock | null;
  round: number;
  loading: boolean;
  kindLoading: boolean;
  inFlight: boolean;
}) {
  return (
    <>
      <hr className="setup-preview-divider" />
      {kind == null ? (
        // Unresolved is its own answer. Without this arm the panel picks a run shape from a
        // node it has not read yet, and every measurement node flashes an LLM trace first.
        <div className="opt-detail-empty">
          {kindLoading
            ? "Reading the optimizer manifest…"
            : "This node is not in the served pipeline."}
        </div>
      ) : kind === "measurement" ? (
        <MeasurementRun block={block} round={round} />
      ) : (
        <CallRun block={block} loading={loading} inFlight={inFlight} />
      )}

      {block && (
        <footer className="opt-detail-footer">
          {/* ONE fold, over the block WHOLE. It replaces a separate `raw input` and `raw output`
              that were proper subsets of it — so this shows strictly more (the model, the usage,
              the timestamp neither of them carried) in half the chrome. Same object the header's
              copy button hands over, so the two cannot disagree about what this node did. */}
          <details className="opt-detail-disclosure">
            <summary>raw block</summary>
            <pre className="opt-detail-pre">{fmtValue(block, { pretty: true })}</pre>
          </details>
        </footer>
      )}
    </>
  );
}

// What an LLM node did: what went in, what came back, and what it cost.
function CallRun({
  block,
  loading,
  inFlight,
}: {
  block: NodeBlock | null;
  loading: boolean;
  inFlight: boolean;
}) {
  const templateFields = block?.input?.template_fields as Record<string, unknown> | undefined;
  const response = block?.output?.response;
  // The model's own thinking channel, when the provider returned one. Its own pane
  // rather than folded into the response blob: it is prose a human reads to understand
  // HOW an answer was reached, and nothing in the loop scores or gates on it.
  const reasoning = typeof block?.output?.reasoning === "string" ? block.output.reasoning : null;
  const usage = block?.usage;
  const variants = variantsOf(response);
  const prefix = prefixReading(
    cacheShare(usage?.cache_read, usage?.input, !!block?.cached),
    !!block?.cached,
  );

  // What we ASKED FOR carries the routing suffix; `block.model` is the provider's echo, which
  // OpenRouter returns bare — so it names a `:nitro` call identically to a normally-routed one.
  // The ask is absent when the node declares no model and takes the provider default.
  const asked = block?.config?.["model"];
  const model = (typeof asked === "string" ? asked : block?.model) || "";

  // Filtered BEFORE the wrapper, never inside each chip: a strip where every entry declines to
  // render must ship no `<div>` at all, or a node that has not fired draws an empty box.
  const chips = [
    { label: "model", value: model },
    { label: "dur", value: block ? fmtSecs(block.duration_s) : "" },
    {
      label: "tokens",
      value: usage
        ? `${usage.input ?? "—"}in / ${usage.output ?? "—"}out / ${(usage.input ?? 0) + (usage.output ?? 0)}t`
        : "",
    },
    // The prefix discount belongs on the prompt pane: it is the number the prompt's FIELD ORDER
    // moves. Labelled "prefix cached", never "cached" — across this app `cached` is the boolean
    // "OUR archive answered", the opposite fact and the one `block.cached` excludes this on.
    {
      label: "prefix cached",
      value:
        prefix.state === "unreported"
          ? "not reported"
          : prefix.share != null
            ? `${fmtPct0(prefix.share)} of prompt`
            : "",
    },
    { label: "template", value: (block?.input?.template_name as string | undefined) ?? "" },
    { label: "ts", value: block?.timestamp ?? "" },
  ].filter((c) => c.value !== "" && c.value !== "—");

  return (
    <>
      {chips.length > 0 && (
        <div className="opt-detail-meta">
          {chips.map((c) => (
            <span key={c.label} className="opt-detail-chip">
              <span className="opt-detail-chip-label">{c.label}</span>
              <span className="opt-detail-chip-value">{c.value}</span>
            </span>
          ))}
        </div>
      )}

      {!block ? (
        <div className="opt-detail-empty">
          {loading
            ? "Loading this round's audit trail…"
            : "This node has not fired in any cached round yet."}
        </div>
      ) : (
        <>
          {/* The population this round, one candidate at a time — the raw blob is
              still one disclosure away in the footer. */}
          {variants && variants.length > 0 && <L1Variants variants={variants} />}

          <div className="opt-detail-cols">
            <section className="opt-detail-col opt-detail-col-fields" aria-label="Template fields">
              <div className="opt-detail-col-head">
                <span>Rendered input</span>
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

            {!variants && (
              <section className="opt-detail-col opt-detail-col-response" aria-label="Response">
                <div className="opt-detail-col-head">
                  <span>Response</span>
                </div>
                <div className="opt-detail-col-body">
                  {response != null ? (
                    <pre className="opt-detail-pre">{fmtValue(response, { pretty: true })}</pre>
                  ) : inFlight ? (
                    <div className="opt-detail-col-empty">
                      In flight — response not yet written.
                    </div>
                  ) : (
                    <div className="opt-detail-col-empty">No response on this block.</div>
                  )}
                </div>
              </section>
            )}

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
        </>
      )}
    </>
  );
}

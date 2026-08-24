"use client";
import { useEffect, useMemo, useRef } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { StaticConnectorProvider, useConnector } from "@/lib/hooks/useConnector";
import { SegmentedControl, Chip, ChipGroup } from "@/components/ui";
import { useSelection } from "@/lib/SelectionContext";
import { targetNodeIds } from "@/lib/terms";
import { PipelineNodeList } from "@/components/dashboard/pipeline/PipelineNodeList";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { interiorNodes, searchPoint } from "@/lib/derivations";

// The pipeline block in "Set up campaign" — the SAME rendering the Chat tab uses
// (inline node list + per-node `NodeSurface`), plus a two-mode toggle. Reuses the
// Chat components verbatim rather than maintaining a second renderer; the only
// new piece is the `LLM only ↔ Research + Match` selector.
//
// Data rides the draft wire — `draft.pipeline_view` + node schemas, fed into a
// `StaticConnectorProvider` (no fetch). The draft is always a check-in here (fresh
// upload OR reuse-origin), and a fresh upload has no committed `datasets/{slug}/`
// yet — so the draft carries its own pipeline render (byte-identical to what Start
// commits), which lets the node editor render before commit instead of hanging on
// "Loading node…". The toggle writes `draft.pipeline_steps`, which commit +
// `derive_optimizer_locks` read.

const LLM_ONLY: string[] = ["llm_only"];

function arraysEqual(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

export function PipelineSetupSection({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  // Seed the connector context from the draft's own render — memoized so the
  // provider value stays stable across unrelated re-renders (the children pull
  // `cv.view` / `cv.nodeConfigSchema` through `useConnector()` unchanged).
  const fields = useMemo(
    () => ({
      connector: draft.connector,
      view: draft.pipeline_view,
      nodeConfigSchema: draft.node_config_schema,
      nodeOutputSchema: draft.node_output_schema,
    }),
    [draft.connector, draft.pipeline_view, draft.node_config_schema, draft.node_output_schema],
  );
  return (
    <StaticConnectorProvider fields={fields}>
      <PipelineSetupInner draft={draft} onApply={onApply} />
    </StaticConnectorProvider>
  );
}

function PipelineSetupInner({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  const cv = useConnector();
  const { node: selected, setSelectionForNode } = useSelection();

  // The model catalogue the allow-list picks from — the union of every model-kind
  // param's options across nodes (server-populated from `PipelineSchema.available_models`).
  const modelCatalogue = useMemo(() => {
    const set = new Set<string>();
    for (const params of Object.values(cv.nodeConfigSchema ?? {})) {
      for (const p of params) if (p.kind === "model") for (const o of p.options) set.add(o);
    }
    return [...set];
  }, [cv.nodeConfigSchema]);
  const allowedModels = draft.allowed_models ?? [];
  // Toggle a model in/out of the origin's allow-list — the full ticked list rides
  // one patch (server replaces wholesale). Unticking to empty is the restrictive
  // default (any human model steer then taints the branch grade C).
  const toggleAllowed = (model: string) =>
    onApply({
      allowed_models: allowedModels.includes(model)
        ? allowedModels.filter((m) => m !== model)
        : [...allowedModels, model],
    });

  // Research+Match preset = the committed pipeline's nodes (stable during setup).
  // `llm_only` isn't in a committed Research+Match view, so its preset is fixed.
  const nodes = interiorNodes(cv.view);
  const researchSteps = nodes.map((n) => n.id);
  const hasResearch = researchSteps.length > 0;
  const isLlmOnly = arraysEqual(draft.optimizer_locks.pipeline, LLM_ONLY);
  // A node detail is only valid for a target-scoped selection that is one of
  // THIS view's nodes (the selection axis is app-global; a Chat-tab selection
  // for another dataset simply won't match, so no stale detail shows).
  const showDetail =
    selected?.scope === "target" && targetNodeIds(cv.view).includes(selected.id);

  // Open the LLM node by default once the view loads — the prompt is the central
  // setup edit, and it lives inside that node's surface (config → prompt →
  // output). One-shot per mount; if the operator closes it, it stays closed.
  const llmNode = nodes.find((n) => n.kind === "llm") ?? null;
  const llmNodeId = llmNode?.id ?? null;
  const autoOpened = useRef(false);
  useEffect(() => {
    if (!autoOpened.current && !isLlmOnly && selected == null && llmNodeId) {
      autoOpened.current = true;
      setSelectionForNode({ id: llmNodeId, scope: "target" });
    }
  }, [isLlmOnly, selected, llmNodeId, setSelectionForNode]);

  return (
    <section className="setup-preview pipeline-setup">
      <header className="setup-preview-head">
        <span className="setup-preview-title">Pipeline</span>
      </header>

      <SegmentedControl
        options={[
          { value: "llm", label: "LLM only" },
          { value: "research", label: "Research + Match", disabled: !hasResearch },
        ]}
        value={isLlmOnly ? "llm" : "research"}
        onChange={(mode) =>
          onApply({ pipeline_steps: mode === "llm" ? LLM_ONLY : researchSteps })
        }
        ariaLabel="Pipeline mode"
      />

      {isLlmOnly ? (
        <>
          <p className="bnode-role">
            Single LLM node — the model answers each query directly from the prompt; no
            retrieval, web search, or matching.
          </p>
          {/* Config + prompt + output as one unit — the search-space surface can't
              render the prompt without its optimizer config (the drift this fixes). */}
          {llmNode ? (
            <NodeSurface
              node={llmNode}
              point={searchPoint(draft.origin_prompt_fields, draft.pipeline_overlay)}
              configSeed={draft.pipeline_overlay}
              schema={cv.nodeConfigSchema}
              outputSchema={cv.nodeOutputSchema}
              mode="search-space"
              flat
              onApply={onApply}
            />
          ) : (
            <small className="config-hint">Loading node…</small>
          )}
        </>
      ) : (
        <>
          <PipelineNodeList
            nodes={interiorNodes(cv.view)}
            schema={cv.nodeConfigSchema}
            scope="target"
          />
          {showDetail && selected ? (
            <NodeDetail
              node={selected}
              draft={draft}
              onClose={() => setSelectionForNode(null)}
              onPromptApply={onApply}
            />
          ) : null}
        </>
      )}

      {/* Origin allow-list: which models a human may steer a fork to without a
          babysit warning. The optimizer never searches the model regardless; this
          governs only the direct human steer. Ticked → clean; unticked → grade C. */}
      {modelCatalogue.length > 0 ? (
        <div className="pipeline-setup-allowed">
          <p className="bnode-role">
            Allowed models — which models a human may steer a fork to without tainting it.
            A fork steered to an unticked model still runs but is marked grade C (not a
            clean measurement). None ticked = nothing sanctioned (any model steer warns).
          </p>
          <ChipGroup label="Allowed models" showLabel>
            {modelCatalogue.map((m) => (
              <Chip
                key={m}
                on={allowedModels.includes(m)}
                onClick={() => toggleAllowed(m)}
                title={`${allowedModels.includes(m) ? "Allowed" : "Not allowed"}: ${m}`}
              >
                {m}
              </Chip>
            ))}
          </ChipGroup>
        </div>
      ) : null}
    </section>
  );
}

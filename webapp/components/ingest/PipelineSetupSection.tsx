"use client";
import { useEffect, useMemo, useRef } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { StaticConnectorProvider, useConnector } from "@/lib/hooks/useConnector";
import { CopyButton, SegmentedControl } from "@/components/ui";
import { useSelection } from "@/lib/SelectionContext";
import { targetNodeIds } from "@/lib/terms";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { interiorNodes, searchPoint } from "@/lib/derivations";

// The pipeline block in "Set up campaign" — the SAME rendering the Chat tab uses
// (inline node list + per-node `NodeSurface`), plus a two-mode toggle. Reuses the
// Chat components verbatim rather than maintaining a second renderer; the only
// new piece is the `LLM only ↔ Research + Match` selector.
//
// Data rides the draft wire into a `StaticConnectorProvider` — a TRANSPORT of the served
// resolution, not a second source: `draft_wire` calls `resolve_pipeline_for_draft`, the check-in
// arm of the resolver `GET /campaigns/{id}/pipeline` serves. It arrives on the response rather
// than being fetched because every draft mutation returns a fresh one, so fetching would put a
// round-trip on each commit-on-blur edit. The toggle writes `draft.pipeline_steps`, which commit +
// `draft_active_steps` read.

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
      reach: draft.reach,
    }),
    [
      draft.connector,
      draft.pipeline_view,
      draft.node_config_schema,
      draft.node_output_schema,
      draft.reach,
    ],
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
  // The two documents the DRAFT owns, and nothing else — the panel's config rows come from the
  // served resolution like everywhere else. Memoized so the panel's props stay stable.
  const authoring = useMemo(
    () => ({ overlay: draft.pipeline_overlay, promptFields: draft.origin_prompt_fields }),
    [draft.pipeline_overlay, draft.origin_prompt_fields],
  );

  // Research+Match preset = the committed pipeline's nodes (stable during setup).
  // `llm_only` isn't in a committed Research+Match view, so its preset is fixed.
  const nodes = interiorNodes(cv.view);
  const researchSteps = nodes.map((n) => n.id);
  const hasResearch = researchSteps.length > 0;
  const isLlmOnly = arraysEqual(draft.active_steps, LLM_ONLY);
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
        {/* The origin as authored, before anything has run — the one form of this document the
            round files never hold, and the only reading of a draft there is. */}
        <CopyButton
          data={{
            pipeline_steps: draft.active_steps,
            resolved_pipeline_params: draft.pipeline_overlay,
            prompt_fields: draft.origin_prompt_fields,
          }}
          title="Copy this origin as JSON"
        />
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

      {/* An unread schema is NOT a locked one. Which params the optimizer may move is
          declared by the backend and nowhere else, so when the probe failed the editor
          below is showing config without permissions — say so rather than let every axis
          render as an operator's choice to pin it. */}
      {draft.schema_source === "unreachable" ? (
        <p className="bnode-role">
          Backend unreachable at check-in, so which params the optimizer may tune is
          unknown — only the backend declares that. Values below are still yours to set;
          reopen this origin once the backend is up to see its real search axes.
        </p>
      ) : null}

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
              overlay={draft.pipeline_overlay}
              isSingleNode={draft.is_single_node}
              schema={cv.nodeConfigSchema}
              outputSchema={cv.nodeOutputSchema}
              mode="search-space"
              modelCapabilities={draft.model_capabilities}
              onApply={onApply}
            />
          ) : (
            <small className="config-hint">Loading node…</small>
          )}
        </>
      ) : (
        <>
          <PipelineFlow
            view={cv.view}
            status={cv.pipelineStatus}
            connector={cv.connector}
            reach={cv.reach}
            scope="target"
            nestsNode={null}
            activeNode={null}
            isLive={false}
            tone="neutral"
          />
          {showDetail && selected ? (
            <NodeDetail
              node={selected}
              authoring={authoring}
              onClose={() => setSelectionForNode(null)}
              onPromptApply={onApply}
            />
          ) : null}
        </>
      )}

    </section>
  );
}

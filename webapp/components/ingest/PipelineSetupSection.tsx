"use client";
import { useEffect, useMemo, useRef } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { StaticConnectorProvider, useConnector } from "@/lib/hooks/useConnector";
import { CopyButton, SegmentedControl, Toolbar, ToolbarSpacer } from "@/components/ui";
import { useSelection } from "@/lib/SelectionContext";
import { targetNodeIds } from "@/lib/terms";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { interiorNodes, searchPoint } from "@/lib/derivations";

// The check-in's pipeline block, on the Chat tab's own renderers. The draft response carries
// the resolver's check-in arm, so it is not fetched: every draft edit returns a fresh one.

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
  const fields = useMemo(
    () => ({
      connector: draft.connector,
      view: draft.pipeline_view,
      // Landed by construction; the context's default (`unbound`) would read as no campaign.
      pipelineStatus: "ok" as const,
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
  // Only the two documents the DRAFT owns; config rows come from the served resolution.
  const authoring = useMemo(
    () => ({ overlay: draft.pipeline_overlay, promptFields: draft.origin_prompt_fields }),
    [draft.pipeline_overlay, draft.origin_prompt_fields],
  );

  const nodes = interiorNodes(cv.view);
  const researchSteps = nodes.map((n) => n.id);
  const hasResearch = researchSteps.length > 0;
  const isLlmOnly = arraysEqual(draft.active_steps, LLM_ONLY);
  // The selection axis is app-global, so match it against THIS view's nodes.
  const showDetail =
    selected?.scope === "target" && targetNodeIds(cv.view).includes(selected.id);

  // One-shot per mount: once closed, it stays closed.
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
      <Toolbar>
        <span className="setup-preview-title">Pipeline</span>
        <ToolbarSpacer />
        <CopyButton
          data={{
            pipeline_steps: draft.active_steps,
            resolved_pipeline_params: draft.pipeline_overlay,
            prompt_fields: draft.origin_prompt_fields,
          }}
          title="Copy this origin as JSON"
        />
      </Toolbar>

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

      {/* An unread schema is NOT a locked one: only the backend declares which params may move. */}
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
          {llmNode ? (
            <NodeSurface
              node={llmNode}
              point={searchPoint(draft.origin_prompt_fields, draft.pipeline_overlay)}
              overlay={draft.pipeline_overlay}
              isSingleNode={draft.is_single_node}
              schema={cv.nodeConfigSchema}
              schemaStatus={cv.pipelineStatus}
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

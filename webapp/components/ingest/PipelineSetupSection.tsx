"use client";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { StaticConnectorProvider, useConnector } from "@/lib/hooks/useConnector";
import { CopyButton } from "@/components/ui";
import { useSelection } from "@/lib/SelectionContext";
import { targetNodeIds } from "@/lib/terms";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { interiorNodes, searchPoint } from "@/lib/derivations";
import { draftPrompts } from "@/lib/prompt-fields";

const LLM_ONLY: string[] = ["llm_only"];

function isLlmOnly(steps: readonly string[]): boolean {
  return steps.length === LLM_ONLY.length && steps.every((x, i) => x === LLM_ONLY[i]);
}

export function pipelineLabel(draft: DraftCampaignWire): string {
  return isLlmOnly(draft.active_steps) ? "LLM only" : `Research + Match · ${draft.active_steps.length} steps`;
}

export function PipelineSetupSection({
  draft,
  onApply,
  children,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
  // What the chosen mode needs, shown under its description and above the node editor.
  children: ReactNode;
}) {
  const fields = useMemo(
    () => ({
      connector: draft.connector,
      view: draft.pipeline_view,
      schema: {
        // Landed by construction; the context's default (`unbound`) would read as no campaign.
        status: "ok" as const,
        config: draft.node_config_schema,
        output: draft.node_output_schema,
        isSingleNode: draft.is_single_node,
      },
      reach: draft.reach,
    }),
    [
      draft.connector,
      draft.pipeline_view,
      draft.node_config_schema,
      draft.node_output_schema,
      draft.is_single_node,
      draft.reach,
    ],
  );
  return (
    <StaticConnectorProvider fields={fields}>
      <PipelineSetupInner draft={draft} onApply={onApply}>
        {children}
      </PipelineSetupInner>
    </StaticConnectorProvider>
  );
}

function PipelineSetupInner({
  draft,
  onApply,
  children,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
  children: ReactNode;
}) {
  const cv = useConnector();
  const { node: selected, setSelectionForNode } = useSelection();
  const authoring = useMemo(
    () => ({ overlay: draft.pipeline_overlay, prompts: draftPrompts(draft.origin_prompts) }),
    [draft.pipeline_overlay, draft.origin_prompts],
  );

  const nodes = interiorNodes(cv.view);
  // The served view can hold `llm_only` itself, and picking only that is no switch at all.
  const researchSteps = nodes.map((n) => n.id).filter((id) => !LLM_ONLY.includes(id));
  const hasResearch = researchSteps.length > 0;
  const llmOnly = isLlmOnly(draft.active_steps);
  // The selection axis is app-global, so match it against THIS view's nodes.
  const showDetail =
    selected?.scope === "target" && targetNodeIds(cv.view).includes(selected.id);

  const llmNode = nodes.find((n) => n.kind === "llm") ?? null;
  const llmNodeId = llmNode?.id ?? null;
  const autoOpened = useRef(false);
  useEffect(() => {
    if (!autoOpened.current && !llmOnly && selected == null && llmNodeId) {
      autoOpened.current = true;
      setSelectionForNode({ id: llmNodeId, scope: "target" });
    }
  }, [llmOnly, selected, llmNodeId, setSelectionForNode]);

  return (
    <>
      <p>
        <label>
          Mode{" "}
          <select
            value={llmOnly ? "llm" : "research"}
            onChange={(e) =>
              onApply({ pipeline_steps: e.target.value === "llm" ? LLM_ONLY : researchSteps })
            }
          >
            <option value="llm">LLM only — one call per query</option>
            <option value="research" disabled={!hasResearch}>
              Research + Match
            </option>
          </select>
        </label>{" "}
        <CopyButton
          data={{
            pipeline_steps: draft.active_steps,
            resolved_params: draft.pipeline_overlay,
            prompts: draft.origin_prompts,
          }}
          title="Copy this origin as JSON"
        />
      </p>

      {children}

      {/* An unread schema is NOT a locked one: only the backend declares which params may move. */}
      {draft.schema_source === "unreachable" ? (
        <p>
          <small>Backend unreachable — which params may be tuned is unknown until it is up.</small>
        </p>
      ) : null}

      {llmOnly ? (
        <>
          {llmNode ? (
            <NodeSurface
              node={llmNode}
              point={searchPoint(authoring.prompts, draft.pipeline_overlay)}
              overlay={draft.pipeline_overlay}
              schema={cv.schema}
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
            layer={{
              view: cv.view,
              status: cv.schema.status,
              connector: cv.connector,
              reach: cv.reach,
              scope: "target",
              nestsNode: null,
              activeNode: null,
              isLive: false,
            }}
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
    </>
  );
}

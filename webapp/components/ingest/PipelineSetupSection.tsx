"use client";
import type { ReactNode } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { useConnector } from "@/lib/hooks/useConnector";
import { CopyButton } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
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

// The draft's pipeline rides the app's `ConnectorProvider`; a node picked here opens the `node` view.
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
  const cv = useConnector();
  const nodes = interiorNodes(cv.view);
  // The served view can hold `llm_only` itself, and picking only that is no switch at all.
  const researchSteps = nodes.map((n) => n.id).filter((id) => !LLM_ONLY.includes(id));
  const hasResearch = researchSteps.length > 0;
  const llmOnly = isLlmOnly(draft.active_steps);
  const llmNode = nodes.find((n) => n.kind === "llm") ?? null;

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
        llmNode ? (
          <NodeSurface
            node={llmNode}
            point={searchPoint(draftPrompts(draft.origin_prompts), draft.pipeline_overlay)}
            overlay={draft.pipeline_overlay}
            schema={cv.schema}
            mode="search-space"
            modelCapabilities={draft.model_capabilities}
            onApply={onApply}
          />
        ) : (
          <small className="config-hint">Loading node…</small>
        )
      ) : (
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
      )}
    </>
  );
}

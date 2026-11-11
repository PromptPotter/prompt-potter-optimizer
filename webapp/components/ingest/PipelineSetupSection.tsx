import type { ReactNode } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api/types";
import { CopyButton, ReadState, Select } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { interiorNodes, nodeSchemaReading, searchPoint } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { draftPrompts } from "@/lib/prompt-fields";

const LLM_ONLY: string[] = ["llm_only"];

function isLlmOnly(steps: readonly string[]): boolean {
  return steps.length === LLM_ONLY.length && steps.every((x, i) => x === LLM_ONLY[i]);
}

export function pipelineLabel(draft: DraftCampaignWire): string {
  return isLlmOnly(draft.active_steps) ? "LLM only" : `Research + Match · ${draft.active_steps.length} steps`;
}

// A node picked here opens the `node` view; a Mode excludes the rest of the draft's chain.
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
  const read = useConnector();
  const schema = nodeSchemaReading(read);
  const llmOnly = isLlmOnly(draft.active_steps);
  // The chain before the draft's exclusions: what runs, and what the config excludes of it.
  const chain = [...draft.active_steps, ...draft.config.exclude_nodes];

  return (
    <ReadState read={read} subject="this check-in's pipeline" className="mech-empty">
      {(pipeline) => {
        const nodes = interiorNodes(pipeline.view);
        // The served view can hold `llm_only` itself, and picking only that is no switch at all.
        const researchSteps = nodes.map((n) => n.id).filter((id) => !LLM_ONLY.includes(id));
        const hasResearch = researchSteps.length > 0;
        const llmNode = nodes.find((n) => n.kind === "llm") ?? null;
        // What each node runs, the dataset's own declaration and the draft's edits laid on.
        const overlay = pipeline.params;
        return (
          <>
            <p>
              <label>
                Mode{" "}
                <Select
                  value={llmOnly ? "llm" : "research"}
                  options={[
                    { value: "llm", label: "LLM only — one call per query" },
                    { value: "research", label: "Research + Match", disabled: !hasResearch },
                  ]}
                  onChange={(mode) => {
                    const runs = mode === "llm" ? LLM_ONLY : researchSteps;
                    onApply({ config: { exclude_nodes: chain.filter((id) => !runs.includes(id)) } });
                  }}
                />
              </label>{" "}
              <CopyButton
                data={{
                  active_steps: draft.active_steps,
                  resolved_params: overlay,
                  prompts: draft.origin_prompts,
                }}
                title="Copy this origin as JSON"
              />
            </p>

            {children}

            {llmOnly ? (
              llmNode ? (
                <NodeSurface
                  node={llmNode}
                  point={searchPoint(draftPrompts(draft.origin_prompts), overlay)}
                  overlay={overlay}
                  schema={schema}
                  mode="search-space"
                  modelCapabilities={pipeline.model_capabilities}
                  onApply={onApply}
                />
              ) : (
                <small className="config-hint">This pipeline serves no LLM node.</small>
              )
            ) : (
              <PipelineFlow
                layer={{
                  view: pipeline.view,
                  read: schema.read,
                  connector: pipeline.connector,
                  reach: pipeline.reach,
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
      }}
    </ReadState>
  );
}

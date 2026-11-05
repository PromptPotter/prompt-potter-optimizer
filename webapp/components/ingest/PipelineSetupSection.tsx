import type { ReactNode } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { CopyButton, ReadState } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { nodeSchemaReading, oneCallNode, searchPoint } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";
import { draftPrompts } from "@/lib/prompt-fields";

export function pipelineLabel(draft: DraftCampaignWire): string {
  return draft.chains.running === "one_call"
    ? "LLM only"
    : `Research + Match · ${draft.active_steps.length} steps`;
}

// A node picked here opens the `node` view; the served Mode is the pick, each sends its served exclusions.
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
  const { chains } = draft;
  const llmOnly = chains.running === "one_call";

  return (
    <ReadState read={read} subject="this check-in's pipeline" className="mech-empty">
      {(pipeline) => {
        // The served one node of the chain, never the view's first: a phase is drawn ahead of it.
        const oneCall = oneCallNode(pipeline);
        // What each node runs, the dataset's own declaration and the draft's edits laid on.
        const overlay = pipeline.params;
        return (
          <>
            <p>
              <label>
                Mode{" "}
                <select
                  value={chains.running}
                  onChange={(e) => {
                    const exclude = e.target.value === "one_call" ? chains.one_call : chains.research;
                    if (exclude) onApply({ config: { exclude_nodes: exclude } });
                  }}
                >
                  <option value="one_call" disabled={!chains.one_call}>
                    LLM only — one call per query
                  </option>
                  <option value="research" disabled={!chains.research}>
                    Research + Match
                  </option>
                </select>
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
              oneCall ? (
                <NodeSurface
                  node={oneCall}
                  point={searchPoint(draftPrompts(draft.origin_prompts), overlay)}
                  overlay={overlay}
                  schema={schema}
                  mode="search-space"
                  modelCapabilities={pipeline.model_capabilities}
                  onApply={onApply}
                />
              ) : (
                <small className="config-hint">This Mode runs no single node.</small>
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

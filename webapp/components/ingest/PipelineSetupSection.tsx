import type { ReactNode } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api/types";
import { CopyButton, ReadState, Select } from "@/components/ui";
import { PipelineFlow } from "@/components/pipeline/PipelineFlow";
import { NodeSurface } from "@/components/node-surface/NodeSurface";
import { draftPrompts, interiorNodes, searchPoint } from "@/lib/derivations";
import { useConnector } from "@/lib/hooks/useConnector";

function stepCount(n: number): string {
  return n === 1 ? "1 step" : `${n} steps`;
}

export function pipelineLabel(draft: DraftCampaignWire): string {
  return `${draft.mode ?? "custom"} · ${stepCount(draft.active_steps.length)}`;
}

// A node picked here opens the `node` view; a Mode sends the exclusions the server serves for it.
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
  const single = draft.active_steps.length === 1;

  return (
    <ReadState read={read} subject="this check-in's pipeline" className="mech-empty">
      {(pipeline) => {
        const llmNode =
          interiorNodes(pipeline.view).find((n) => n.kind === "llm" && draft.active_steps.includes(n.id)) ?? null;
        // What each node runs, the dataset's own declaration and the draft's edits laid on.
        const overlay = pipeline.params;
        return (
          <>
            <p>
              {draft.modes.length > 0 ? (
                <label>
                  Mode{" "}
                  <Select
                    value={draft.mode ?? ""}
                    options={draft.modes.map((m) => ({
                      value: m.name,
                      label: `${m.name} · ${stepCount(m.steps.length)}`,
                      disabled: m.exclude_nodes === null,
                    }))}
                    onChange={(name) => {
                      const picked = draft.modes.find((m) => m.name === name);
                      if (picked?.exclude_nodes) onApply({ config: { exclude_nodes: picked.exclude_nodes } });
                    }}
                  />
                </label>
              ) : null}{" "}
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

            {single ? (
              llmNode ? (
                <NodeSurface
                  node={llmNode}
                  point={searchPoint(draftPrompts(draft.origin_prompts), overlay)}
                  overlay={overlay}
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
                  read,
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

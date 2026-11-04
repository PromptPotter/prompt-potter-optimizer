import { optimizerRosterRead, type DraftCampaignWire, type DraftPatch } from "@/lib/api";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useRead } from "@/lib/hooks/useRead";
import { useWorkspace } from "@/lib/workspace";
import { ReadState, SegmentedControl } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeKnobsPanel } from "@/components/dashboard/pipeline/NodeKnobsPanel";

export function OptimizerSetupSection({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  const { optimizer } = draft.optimization_overrides;
  const { doc, status } = useOptimizerPipeline();
  const rosterRead = useRead(optimizerRosterRead());
  const { close } = useWorkspace();

  return (
    <>
      <p className="bnode-role">
        Which optimizer searches this campaign. Every one is scored, stopped and billed by the
        same bench, so their results compare. Switching starts the new one at its own defaults.
      </p>

      <ReadState read={rosterRead} subject="the optimizers this install runs" className="mech-empty">
        {(roster) => {
          const entry = roster.optimizers.find((o) => o.name === optimizer);
          return (
            <div>
              <SegmentedControl<string>
                options={roster.optimizers.map((o) => ({
                  value: o.name,
                  label: o.name === roster.default ? `${o.name} · default` : o.name,
                }))}
                value={optimizer}
                onChange={(name) => {
                  if (name === optimizer) return;
                  close("node");
                  onApply({ optimization_overrides: { optimizer: name } });
                }}
                ariaLabel="Optimizer"
              />
              <p>
                {!entry
                  ? null
                  : entry.paper
                    ? `Runs ${entry.paper} at its paper configuration, version ${entry.version}.`
                    : entry.origin === null
                      ? `PromptPotter's own optimizer, version ${entry.version}.`
                      : `Installed from ${entry.origin}, version ${entry.version}.`}
              </p>
            </div>
          );
        }}
      </ReadState>

      <NodeKnobsPanel onChange={(patch) => onApply({ optimization_overrides: { nodes: patch } })} />

      <PipelineFlow
        layer={{
          view: doc?.view ?? null,
          status,
          connector: "PromptPotter",
          reach: draft.optimizer_setup.reach,
          scope: "optimizer",
          nestsNode: doc?.measurement_node ?? null,
          activeNode: null,
          isLive: false,
        }}
        tone="neutral"
      />
    </>
  );
}

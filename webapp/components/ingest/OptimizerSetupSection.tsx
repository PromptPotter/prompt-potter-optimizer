import { optimizerRosterRead } from "@/lib/api";
import type { DraftPatch } from "@/lib/api/types";
import { BRAND } from "@/lib/brand";
import { useConnector } from "@/lib/hooks/useConnector";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { useWorkspace } from "@/lib/workspace";
import { ReadState, SegmentedControl } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeKnobsPanel } from "@/components/dashboard/pipeline/NodeKnobsPanel";

// Reads the connector read its knobs panel and graph read: a check-in's draft is its transport.
export function OptimizerSetupSection({ onApply }: { onApply: (patch: DraftPatch) => void }) {
  const pipeline = shownData(useConnector());
  const optimizer = pipeline?.optimizer ?? null;
  const setup = pipeline?.optimizer_setup ?? null;
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
                value={optimizer ?? ""}
                onChange={(name) => {
                  if (name === optimizer) return;
                  close("node");
                  onApply({ config: { optimization: { optimizer: name } } });
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

      <NodeKnobsPanel onChange={(patch) => onApply({ config: { optimization: { nodes: patch } } })} />

      {setup && (
        <PipelineFlow
          layer={{
            view: setup.view,
            read: { status: "ready", data: setup },
            connector: BRAND.provider.name,
            reach: setup.reach,
            scope: "optimizer",
            nestsNode: setup.measurement_node,
            activeNode: null,
            isLive: false,
          }}
          tone="neutral"
        />
      )}
    </>
  );
}

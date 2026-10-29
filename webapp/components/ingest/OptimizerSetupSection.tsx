"use client";
import { useMemo } from "react";
import { optimizerRosterRead, type DraftCampaignWire, type DraftPatch } from "@/lib/api";
import { StaticConnectorProvider, useConnector } from "@/lib/hooks/useConnector";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useRead } from "@/lib/hooks/useRead";
import { useSelection } from "@/lib/SelectionContext";
import { ReadState, SegmentedControl } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeKnobsPanel } from "@/components/dashboard/pipeline/NodeKnobsPanel";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";

export function OptimizerSetupSection({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  const { optimizer } = draft.optimization_overrides;
  const setup = draft.optimizer_setup;
  const fields = useMemo(
    () => ({
      optimizer,
      optimizerSetup: setup,
      schema: { status: "ok" as const, config: null, output: null, isSingleNode: false },
    }),
    [optimizer, setup],
  );
  return (
    <StaticConnectorProvider fields={fields}>
      <OptimizerSetupInner draft={draft} onApply={onApply} />
    </StaticConnectorProvider>
  );
}

function OptimizerSetupInner({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  const { optimizer } = draft.optimization_overrides;
  const { doc, failure } = useOptimizerPipeline(useConnector().optimizer);
  const rosterRead = useRead(optimizerRosterRead());
  const { node: selected, setSelectionForNode } = useSelection();
  // Match on SCOPE too: node ids are not disjoint across pipelines (self-optimization shares all).
  const shown = selected?.scope === "optimizer" ? selected : null;

  return (
    <>
      <p className="bnode-role">
        Which optimizer searches this campaign. Every one is scored, stopped and billed by the
        same bench, so their results compare. Switching drops the knobs you changed on the
        previous one.
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
                  setSelectionForNode(null);
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

      <PipelineFlow
        layer={{
          view: doc?.view ?? null,
          status: doc ? "ok" : failure ? "error" : "loading",
          connector: "PromptPotter",
          reach: doc?.reach ?? null,
          scope: "optimizer",
          nestsNode: doc?.measurement_node ?? null,
          activeNode: null,
          isLive: false,
        }}
        tone="neutral"
      />

      {shown ? <NodeDetail node={shown} onClose={() => setSelectionForNode(null)} /> : null}

      <h4>{optimizer} knobs</h4>
      <NodeKnobsPanel onChange={(patch) => onApply({ optimization_overrides: { nodes: patch } })} />
    </>
  );
}

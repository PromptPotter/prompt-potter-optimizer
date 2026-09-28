"use client";
import { useMemo } from "react";
import { fetchOptimizerRoster, type DraftCampaignWire, type DraftPatch } from "@/lib/api";
import { measurementNode } from "@/lib/derivations";
import { StaticConnectorProvider, useConnector } from "@/lib/hooks/useConnector";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useSelection } from "@/lib/SelectionContext";
import { SegmentedControl, Toolbar, ToolbarSpacer } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeKnobsPanel } from "@/components/dashboard/control/NodeKnobsPanel";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";

// The check-in's optimizer: which manifest this campaign runs, its loop, and its knobs. Not
// `PipelineStack`: the level below is `PipelineSetupSection`'s. The roster is served, never listed.

export function OptimizerSetupSection({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  const { optimizer } = draft.optimization_overrides;
  // The draft response's own field, so the node detail below reads the manifest this draft picked.
  const fields = useMemo(() => ({ optimizer, pipelineStatus: "ok" as const }), [optimizer]);
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
  const { optimizer, nodes } = draft.optimization_overrides;
  const { doc, error } = useOptimizerPipeline(useConnector().optimizer);
  const rosterRead = useRead(
    { key: "optimizers", fetch: (signal) => fetchOptimizerRoster(signal) },
    { surface: "optimizer-roster" },
  );
  const roster = readyData(rosterRead);
  const { node: selected, setSelectionForNode } = useSelection();
  // Match on SCOPE too: node ids are not disjoint across pipelines (self-optimization shares all).
  const shown = selected?.scope === "optimizer" ? selected : null;
  const entry = roster?.optimizers.find((o) => o.name === optimizer);

  return (
    <section className="setup-preview">
      <Toolbar>
        <span className="setup-preview-title">Optimizer</span>
        <ToolbarSpacer />
        <span className="setup-preview-sub">the loop that searches</span>
      </Toolbar>
      <p className="bnode-role">
        Which optimizer searches this campaign. Every one is scored, stopped and billed by the
        same bench, so their results compare. Switching drops the knobs you changed on the
        previous one.
      </p>

      {roster ? (
        <div className="optimizer-pick">
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
          <p className="optimizer-pick-cite">
            {entry?.paper
              ? `Runs ${entry.paper} at its paper configuration, version ${entry.version}.`
              : entry
                ? `PromptPotter's own optimizer, version ${entry.version}.`
                : null}
          </p>
        </div>
      ) : (
        <p className="mech-empty">
          {rosterRead.status === "failed"
            ? "Could not load the optimizers this install runs."
            : "Loading optimizers…"}
        </p>
      )}

      <PipelineFlow
        view={doc?.view ?? null}
        status={doc ? "ok" : error ? "error" : "loading"}
        connector="PromptPotter"
        reach={doc?.reach ?? null}
        scope="optimizer"
        nestsNode={measurementNode(doc)}
        activeNode={null}
        isLive={false}
        tone="neutral"
      />

      {shown ? <NodeDetail node={shown} onClose={() => setSelectionForNode(null)} /> : null}

      <details className="new-campaign-optional">
        <summary>{optimizer} knobs</summary>
        <div className="new-campaign-optional-body">
          <NodeKnobsPanel
            optimizer={optimizer}
            nodes={nodes}
            onChange={(patch) => onApply({ optimization_overrides: { nodes: patch } })}
          />
        </div>
      </details>
    </section>
  );
}

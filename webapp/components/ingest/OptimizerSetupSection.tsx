"use client";
import { useMemo } from "react";
import {
  optimizerRosterRead,
  type DraftCampaignWire,
  type DraftPatch,
  type NodePrompt,
  type OptimizerSetup,
} from "@/lib/api";
import { StaticConnectorProvider, useConnector } from "@/lib/hooks/useConnector";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useRead } from "@/lib/hooks/useRead";
import { useSelection } from "@/lib/SelectionContext";
import { draftPrompts } from "@/lib/prompt-fields";
import { ReadState, SegmentedControl } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeKnobsPanel } from "@/components/dashboard/pipeline/NodeKnobsPanel";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { PromptFieldsEditor } from "@/components/shell/node-surface/PromptFieldsEditor";
import { SnippetLibrary, type PromptTarget } from "./SnippetLibrary";

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

// The optimizer's prompts as the one map `PromptFieldsEditor` builds a patch over, a draft carrying no shots.
function optimizerPrompts(setup: OptimizerSetup): Record<string, NodePrompt> {
  return Object.fromEntries(
    Object.entries(setup.prompts).map(([node, p]) => [node, { fields: p.fields, shots: [] }]),
  );
}

// Every prompt field a snippet may land in: each target step's served `prompt` rows, then each
// optimizer node running a prompt.
function promptTargets(draft: DraftCampaignWire): PromptTarget[] {
  const target = draftPrompts(draft.origin_prompts);
  const optimizer = optimizerPrompts(draft.optimizer_setup);
  return [
    ...Object.entries(draft.node_config_schema)
      .map(([node, rows]) => ({
        scope: "target" as const,
        node,
        fields: rows.filter((r) => r.kind === "prompt").map((r) => r.key),
        prompts: target,
      }))
      .filter((t) => t.fields.length > 0),
    ...Object.entries(draft.optimizer_setup.prompts).map(([node, p]) => ({
      scope: "optimizer" as const,
      node,
      fields: Object.keys(p.fields),
      prompts: optimizer,
    })),
  ];
}

function OptimizerSetupInner({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  const { optimizer } = draft.optimization_overrides;
  const setup = draft.optimizer_setup;
  const { doc, failure } = useOptimizerPipeline(useConnector().optimizer);
  const rosterRead = useRead(optimizerRosterRead());
  const { node: selected, setSelectionForNode } = useSelection();
  // Match on SCOPE too: node ids are not disjoint across pipelines (self-optimization shares all).
  const shown = selected?.scope === "optimizer" ? selected : null;
  const prompts = useMemo(() => optimizerPrompts(setup), [setup]);
  const targets = useMemo(() => promptTargets(draft), [draft]);

  return (
    <>
      <p className="bnode-role">
        Which optimizer searches this campaign. Every one is scored, stopped and billed by the
        same bench, so their results compare. Switching keeps the knobs you changed, so an
        optimizer that lacks a node you edited refuses the switch.
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

      <NodeKnobsPanel onChange={(patch) => onApply({ optimization_overrides: { nodes: patch } })} />

      {Object.keys(setup.prompts).length > 0 ? (
        <div className="optimizer-prompts">
          <h4>{optimizer} prompts</h4>
          {Object.entries(setup.prompts).map(([node, p]) => (
            <div key={node} className="optimizer-prompt">
              <PromptFieldsEditor
                scope="optimizer"
                node={node}
                prompts={prompts}
                fields={Object.keys(p.fields)}
                edited={p.edited}
                onApply={onApply}
                locks={{}}
                onLock={undefined}
              />
              {p.versions_declared > 1 ? (
                <p className="inspector-note">
                  Prompt {p.version} of the {p.versions_declared} this node declares.
                </p>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      <SnippetLibrary targets={targets} onApply={onApply} />

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
    </>
  );
}

"use client";
import { measurementNode } from "@/lib/derivations";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useSelection } from "@/lib/SelectionContext";
import { Toolbar, ToolbarSpacer } from "@/components/ui";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";

// The optimizer loop on the setup surface. Not `PipelineStack`: the level below is already
// drawn by `PipelineSetupSection`. Read-only — there is no per-campaign optimizer overlay.

export function OptimizerSetupSection() {
  const { doc, error } = useOptimizerPipeline();
  const { node: selected, setSelectionForNode } = useSelection();

  const view = doc?.view ?? null;
  // Match on SCOPE too: node ids are not disjoint across pipelines (self-optimization shares all).
  const shown = selected?.scope === "optimizer" ? selected : null;

  return (
    <section className="setup-preview">
      <Toolbar>
        <span className="setup-preview-title">Optimizer</span>
        <ToolbarSpacer />
        <span className="setup-preview-sub">the loop that searches</span>
      </Toolbar>
      <p className="bnode-role">
        The evolution loop itself — generate, score, critique, and the two escalation
        steps it reaches for when a round stalls. Pick a node to read what it runs on.
        These are set once for this install, not per campaign.
      </p>

      <PipelineFlow
        view={view}
        status={doc ? "ok" : error ? "error" : "loading"}
        connector="PromptPotter"
        reach={doc?.reach ?? null}
        scope="optimizer"
        nestsNode={measurementNode(doc)}
        activeNode={null}
        isLive={false}
        tone="neutral"
      />

      {shown ? (
        <NodeDetail node={shown} onClose={() => setSelectionForNode(null)} />
      ) : null}
    </section>
  );
}

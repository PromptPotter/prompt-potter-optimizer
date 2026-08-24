"use client";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useSelection } from "@/lib/SelectionContext";
import { PipelineFlow } from "@/components/dashboard/pipeline/PipelineFlow";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";

// The OPTIMIZER, on the setup surface — the loop that is about to search, shown while
// the operator is still deciding what to search over. `PipelineSetupSection` beside it
// answers "what gets optimized"; this one answers "what does the optimizing", and until
// now the only trace of it here was a `Max rounds` number field.
//
// It draws through `PipelineFlow` — the SAME renderer the chat hero uses for the
// optimizer level of its stack — rather than a list of its own. Not `PipelineStack`,
// which is the zoom CHAIN: the level below the optimizer is this campaign's pipeline,
// and that is already on screen in `PipelineSetupSection` directly underneath. Drawing
// the chain here would render it twice.
//
// `/optimizer-pipeline` is a static manifest with no campaign in it, which is what makes
// this reachable before mint — a draft has no cycle to read a pipeline from.
//
// The picked node opens the ONE `NodeDetail` — the same panel the chat hero and the
// dashboard canvas open, so config and the prompt each node STARTS from read here
// exactly as they do there. It used to hand-roll a header, a close button and a bare
// config editor, which showed the knobs and silently dropped the prompt.
//
// Node config is READ-ONLY here, and that is a boundary rather than an omission:
// `OptimizationConfig` declares no optimizer-node field and `assets/optimizer/
// pipeline.yaml` is one repo-wide operator-owned file, so there is no per-campaign
// optimizer overlay for an edit to land in. Offering an input would be a control that
// writes nowhere.

export function OptimizerSetupSection() {
  const { doc, error } = useOptimizerPipeline();
  const { node: selected, setSelectionForNode } = useSelection();

  const view = doc?.view ?? null;
  const schema = doc?.node_config_schema ?? null;
  // The selection axis is app-global and node ids are not disjoint across pipelines, so
  // match on the SCOPE as well as the id — a `target` click in the section below must
  // not open an optimizer node that happens to share its name (on a self-optimizing
  // campaign every one of them does).
  const shown = selected?.scope === "optimizer" ? selected : null;

  return (
    <section className="setup-preview">
      <header className="setup-preview-head">
        <span className="setup-preview-title">Optimizer</span>
        <span className="setup-preview-side">
          <span className="setup-preview-sub">the loop that searches</span>
        </span>
      </header>
      <p className="bnode-role">
        The evolution loop itself — generate, score, critique, and the two escalation
        steps it reaches for when a round stalls. Pick a node to read what it runs on.
        These are set once for this install, not per campaign.
      </p>

      <PipelineFlow
        view={view}
        status={doc ? "ok" : error ? "error" : "loading"}
        connector="PromptPotter"
        schema={schema}
        scope="optimizer"
        nestsNode={null}
        activeNode={null}
        isLive={false}
        tone="neutral"
      />

      {shown ? (
        <NodeDetail node={shown} draft={null} onClose={() => setSelectionForNode(null)} />
      ) : null}
    </section>
  );
}

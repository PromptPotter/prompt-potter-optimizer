"use client";
import { useEffect, useRef } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { ConnectorProvider, useConnector } from "@/lib/hooks/useConnector";
import { useSelection } from "@/lib/SelectionContext";
import { targetNodeIds } from "@/lib/terms";
import { cx } from "@/lib/cx";
import { PipelineNodeList } from "@/components/dashboard/pipeline/PipelineNodeList";
import { BackendNodeDetail } from "@/components/dashboard/pipeline/BackendNodeDetail";
import { PromptFieldsEditor } from "@/components/dashboard/control/PromptFieldsEditor";
import { NodeLockEditor } from "@/components/dashboard/control/NodeLockEditor";

// The pipeline block in "Set up campaign" — the SAME rendering the Chat tab uses
// (inline node list + per-node `NodeSurface`), plus a two-mode toggle. Reuses the
// Chat components verbatim rather than maintaining a second renderer; the only
// new piece is the `LLM only ↔ Research + Match` selector.
//
// Data rides `GET /datasets/{slug}/pipeline` via a nested `ConnectorProvider`
// keyed to the draft's slug. For a reuse-origin that slug IS a committed dataset,
// so the endpoint returns the full pipeline view + per-node config/output schema
// (stable through setup — the file isn't rewritten until Start). The toggle
// writes `draft.pipeline_steps`, which commit + `derive_optimizer_locks` read.

const LLM_ONLY: string[] = ["llm_only"];

function arraysEqual(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

export function PipelineSetupSection({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  return (
    <ConnectorProvider datasetName={draft.slug}>
      <PipelineSetupInner draft={draft} onApply={onApply} />
    </ConnectorProvider>
  );
}

function PipelineSetupInner({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  const cv = useConnector();
  const { node: selected, setSelectionForNode } = useSelection();

  // Research+Match preset = the committed pipeline's nodes (stable during setup).
  // `llm_only` isn't in a committed Research+Match view, so its preset is fixed.
  const nodes = (cv.view?.nodes ?? []).filter((n) => n.kind !== "io");
  const researchSteps = nodes.map((n) => n.id);
  const hasResearch = researchSteps.length > 0;
  const isLlmOnly = arraysEqual(draft.optimizer_locks.pipeline, LLM_ONLY);
  // A node detail is only valid when the selection is one of THIS view's nodes
  // (the selection axis is app-global; a Chat-tab selection for another dataset
  // simply won't match, so no stale detail shows).
  const showDetail = selected != null && targetNodeIds(cv.view).includes(selected);

  // Open the LLM node by default once the view loads — the prompt is the central
  // setup edit, and it lives inside that node's surface (config → prompt →
  // output). One-shot per mount; if the operator closes it, it stays closed.
  const llmNodeId = nodes.find((n) => n.kind === "llm")?.id ?? null;
  const autoOpened = useRef(false);
  useEffect(() => {
    if (!autoOpened.current && !isLlmOnly && selected == null && llmNodeId) {
      autoOpened.current = true;
      setSelectionForNode(llmNodeId);
    }
  }, [isLlmOnly, selected, llmNodeId, setSelectionForNode]);

  return (
    <section className="setup-preview pipeline-setup">
      <header className="setup-preview-head">
        <span className="setup-preview-title">Pipeline</span>
      </header>

      <div className="pipeline-mode-toggle" role="group" aria-label="Pipeline mode">
        <button
          type="button"
          className={cx("pmode", isLlmOnly && "is-active")}
          aria-pressed={isLlmOnly}
          onClick={() => onApply({ pipeline_steps: LLM_ONLY })}
        >
          LLM only
        </button>
        <button
          type="button"
          className={cx("pmode", !isLlmOnly && "is-active")}
          aria-pressed={!isLlmOnly}
          disabled={!hasResearch}
          onClick={() => hasResearch && onApply({ pipeline_steps: researchSteps })}
        >
          Research + Match
        </button>
      </div>

      {isLlmOnly ? (
        <>
          <p className="bnode-role">
            Single LLM node — the model answers each query directly from the prompt; no
            retrieval, web search, or matching.
          </p>
          {llmNodeId && (cv.nodeConfigSchema?.[llmNodeId]?.length ?? 0) > 0 ? (
            <NodeLockEditor
              node={llmNodeId}
              params={cv.nodeConfigSchema![llmNodeId]}
              overlayBase={(draft.pipeline_overlay ?? {}) as Record<string, unknown>}
              lockModel={draft.lock_model}
              onApply={onApply}
            />
          ) : null}
          <PromptFieldsEditor value={draft.origin_prompt_fields} onApply={onApply} flat />
        </>
      ) : (
        <>
          <PipelineNodeList />
          {showDetail ? (
            <BackendNodeDetail
              draft={draft}
              onClose={() => setSelectionForNode(null)}
              onPromptApply={onApply}
            />
          ) : null}
        </>
      )}
    </section>
  );
}

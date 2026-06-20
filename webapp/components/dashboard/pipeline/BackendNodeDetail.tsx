"use client";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { resolveSearchPoint } from "@/lib/derivations";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useConnector } from "@/lib/hooks/useConnector";
import { NodeSurface } from "./NodeSurface";

// Read-only detail for the target node clicked in TargetPipelineHero. It resolves
// WHICH searchpoint to show by context — a draft in origin setup, the live
// in-flight candidate, or the dataset origin (`resolveSearchPoint`) — then
// dispatches on the SELECTED node's kind: a concrete node renders its
// search-space config + output + (for `llm`) prompt; the synthetic single-"llm"
// demo chip (an id with no matching view node) renders the whole-pipeline values
// view. Both are the one `NodeSurface`.
//
// Steering is a separate act with its own home — `ScoringInspector` opens the
// `SteerForkPanel` Dialog. This panel is inspect-only; it never forks.

interface Props {
  // The active draft while a campaign is being set up; null otherwise. When set,
  // the preview shows the draft's searchpoint rather than the origin / live one.
  draft: DraftCampaignWire | null;
  onClose: () => void;
  // Setup only: makes the LLM node's prompt editable (persists via this patch).
  onPromptApply?: (patch: DraftPatch) => void;
}

export function BackendNodeDetail({ draft, onClose, onPromptApply }: Props) {
  // `cv` self-sourced from the nearest ConnectorProvider — the shell connector
  // on the Chat tab, the draft's nested connector in the ingest setup section.
  const cv = useConnector();
  const { dash } = useDashboard();
  const { node: selectedId } = useSelection();
  const { point, label } = resolveSearchPoint({ draft, dash, cv });
  const node = cv.view?.nodes.find((n) => n.id === selectedId && n.kind !== "io") ?? null;

  // No concrete view node behind the selection (the synthetic single-LLM chip) —
  // show the whole-pipeline values view (the candidate's evolved config), read-only.
  if (!node) {
    return (
      <NodeSurface
        node={null}
        point={point}
        configSeed={point.pipeline_overlay}
        schema={cv.nodeConfigSchema}
        outputSchema={cv.nodeOutputSchema}
        label={label}
        mode="values"
        readOnly
        onClose={onClose}
      />
    );
  }

  // The search-space editor's editable state rides the draft (overlay of
  // in-progress edits + the campaign-wide model lock). Inspection (no draft)
  // derives the model-lock state from the served schema's model-param
  // `optimizer_tunable` (== not forbidden_strict) and renders read-only.
  const configSeed = (draft?.pipeline_overlay ?? {}) as Record<string, unknown>;
  const params = cv.nodeConfigSchema?.[node.id] ?? [];
  const modelTunable = params.find((p) => p.kind === "model")?.optimizer_tunable ?? false;
  const lockModel = draft ? draft.optimization_overrides.lock_model : !modelTunable;

  return (
    <NodeSurface
      node={node}
      point={point}
      configSeed={configSeed}
      schema={cv.nodeConfigSchema}
      outputSchema={cv.nodeOutputSchema}
      label={label}
      mode="search-space"
      lockModel={lockModel}
      onClose={onClose}
      onApply={onPromptApply}
    />
  );
}

"use client";
import type { ConnectorView } from "@/lib/types/connector";
import type { DashboardSnapshot } from "@/lib/poll";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { resolveSearchPoint } from "@/lib/derivations/searchPoint";
import { useSelection } from "@/lib/SelectionContext";
import { SearchPointPreview } from "./SearchPointPreview";
import { NodeSurface } from "./NodeSurface";

// Read-only detail for the target node clicked in TargetPipelineHero. It resolves
// WHICH searchpoint to show by context — a draft in origin setup, the live
// in-flight candidate, or the dataset origin (`resolveSearchPoint`) — then
// dispatches on the SELECTED node's kind: every node renders its config + output
// contract via NodeSurface; `llm` nodes additionally carry the prompt. The
// synthetic single-"llm" demo chip (an id with no matching view node) falls back
// to the whole-pipeline SearchPointPreview.
//
// Steering is a separate act with its own home — `ScoringInspector` opens the
// `SteerForkPanel` Dialog. This panel is inspect-only; it never forks.

interface Props {
  cv: ConnectorView;
  dash: DashboardSnapshot | null;
  // The active draft while a campaign is being set up; null otherwise. When set,
  // the preview shows the draft's searchpoint rather than the origin / live one.
  draft: DraftCampaignWire | null;
  onClose: () => void;
  // Setup only: makes the LLM node's prompt editable (persists via this patch).
  onPromptApply?: (patch: DraftPatch) => void;
}

export function BackendNodeDetail({ cv, dash, draft, onClose, onPromptApply }: Props) {
  const { node: selectedId } = useSelection();
  const { point, label } = resolveSearchPoint({ draft, dash, cv });
  const node = cv.view?.nodes.find((n) => n.id === selectedId && n.kind !== "io") ?? null;

  // No concrete view node behind the selection (the synthetic single-LLM chip) —
  // show the whole-pipeline searchpoint as before.
  if (!node) {
    return (
      <SearchPointPreview
        point={point}
        schema={cv.nodeConfigSchema}
        outputSchema={cv.nodeOutputSchema}
        label={label}
        onClose={onClose}
      />
    );
  }

  // The lock editor's editable state rides the draft (overlay of in-progress
  // edits + the campaign-wide model lock). Inspection (no draft) derives the
  // model-lock state from the served schema's model-param `optimizer_tunable`
  // (== not forbidden_strict) and renders read-only.
  const overlayBase = (draft?.pipeline_overlay ?? {}) as Record<string, unknown>;
  const params = cv.nodeConfigSchema?.[node.id] ?? [];
  const modelTunable = params.find((p) => p.kind === "model")?.optimizer_tunable ?? false;
  const lockModel = draft ? draft.lock_model : !modelTunable;

  return (
    <NodeSurface
      node={node}
      point={point}
      schema={cv.nodeConfigSchema}
      outputSchema={cv.nodeOutputSchema}
      label={label}
      overlayBase={overlayBase}
      lockModel={lockModel}
      onClose={onClose}
      onApply={onPromptApply}
    />
  );
}

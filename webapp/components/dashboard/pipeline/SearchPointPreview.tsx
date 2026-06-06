"use client";
import type { NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import type { CandidateSearchPoint } from "@/lib/derivations/candidateSearchPoint";
import { PromptFieldsEditor } from "@/components/dashboard/control/PromptFieldsEditor";
import { NodeConfigEditor } from "@/components/dashboard/control/NodeConfigEditor";
import { NodeOutputSchemaView } from "@/components/dashboard/control/NodeOutputSchemaView";

// The single read-only searchpoint preview — node config (model + params) +
// structured-output contract + the six prompt fields, rendered as one unit. The
// connector control panel (`BackendNodeDetail`) feeds it a context-resolved
// `CandidateSearchPoint` (draft / live in-flight / origin), so the inspect-the-
// node surface reads identically to the steer panel's editor regardless of which
// searchpoint is in view. `label` names which one (setup / live candidate N /
// origin). Inspect-only — never forks.
export function SearchPointPreview({
  point,
  schema,
  outputSchema,
  label,
  onClose,
}: {
  point: CandidateSearchPoint;
  schema: Record<string, NodeConfigParam[]> | null;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
  label: string;
  onClose?: () => void;
}) {
  return (
    <div className="bnode">
      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">Pipeline, optimizer &amp; prompt</span>
          <span className="setup-preview-side">
            <span className="setup-preview-sub">{label}</span>
            {onClose ? (
              <button
                type="button"
                className="bnode-close"
                onClick={onClose}
                aria-label="Close detail"
                title="Close"
              >
                ×
              </button>
            ) : null}
          </span>
        </header>

        <NodeConfigEditor schema={schema} seedOverlay={point.pipeline_overlay} readOnly />

        <NodeOutputSchemaView schema={outputSchema} />

        <hr className="setup-preview-divider" />

        {/* Always render the fields — when the searchpoint ships no prompt the
            six labelled fields show empty (with hint placeholders), so the
            operator still sees the prompt structure to reason against. */}
        <PromptFieldsEditor value={point.origin_prompt_fields} readOnly flat />
      </section>
    </div>
  );
}

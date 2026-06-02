"use client";
import type { ConnectorView } from "@/lib/types/connector";
import { PromptFieldsEditor } from "@/components/dashboard/control-panel/PromptFieldsEditor";
import { NodeConfigEditor } from "@/components/dashboard/control-panel/NodeConfigEditor";
import { NodeOutputSchemaView } from "@/components/dashboard/control-panel/NodeOutputSchemaView";

// Read-only config detail for a *target/backend* node, opened by clicking the
// node in TargetPipelineHero. Renders the whole node as one unit — config
// (model + params + provider) + prompt + structured output — straight off the
// server's `node_config_schema` / `node_output_schema` (built from
// `pipeline.json`), so the inspect-the-node surface reads identically to the
// steer panel's editor. No new fetch: it rides the shared `useConnector()` view.
//
// Steering is a separate act with its own home — the `ScoringInspector` opens
// the `SteerForkPanel` Dialog directly when a candidate is selected. This panel
// is inspect-only; it never forks.

interface Props {
  cv: ConnectorView;
  onClose: () => void;
}

export function BackendNodeDetail({ cv, onClose }: Props) {
  const startingPrompt = cv.startingPrompt;

  return (
    <div className="bnode">
      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">Pipeline, optimizer &amp; prompt</span>
          <span className="setup-preview-side">
            <span className="setup-preview-sub">
              {cv.isLive ? "read-only — running" : "read-only"}
            </span>
            <button
              type="button"
              className="bnode-close"
              onClick={onClose}
              aria-label="Close detail"
              title="Close"
            >
              ×
            </button>
          </span>
        </header>

        <NodeConfigEditor schema={cv.nodeConfigSchema} seedOverlay={{}} readOnly />

        <NodeOutputSchemaView schema={cv.nodeOutputSchema} />

        <hr className="setup-preview-divider" />

        {/* Always render the fields — when the dataset ships no authored prompt
            the six labelled fields show empty (with their hint placeholders), so
            the operator sees the prompt structure to reason against. */}
        <PromptFieldsEditor value={startingPrompt ?? {}} demo readOnly flat />
      </section>
    </div>
  );
}

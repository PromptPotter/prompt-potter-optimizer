"use client";
import type { OptimizerLocks } from "@/lib/api";
import type { ConnectorView } from "@/lib/types/connector";
import { lockedParams } from "@/lib/optimizer-locks";
import { PipelineConfigEditor } from "@/components/dashboard/ingest/PipelineConfigEditor";
import { PromptFieldsEditor } from "@/components/dashboard/ingest/PromptFieldsEditor";
import { LockTable } from "@/components/dashboard/ingest/LockTable";

// Read-only config detail for a *target/backend* node, opened by clicking the
// node in TargetPipelineHero. This IS the setup flow's Step-3 surface
// (`PipelinePromptStep`'s `.setup-preview` block) — the same `PipelineConfigEditor`
// + `PromptFieldsEditor`, rendered read-only — so it reads identically. It's the
// on-the-node home of the old floating ConfigMenu's "frozen parameters", now
// derived from the real overlay instead of a hardcoded map.
//
// Data is whatever `useConnectorView` already fetched: `optimizerLocks` (the
// minted-pipeline permission surface) + `startingPrompt` (origin seed). No new
// fetch. Prompt is origin-only — the current/evolved prompt isn't on any
// webapp-readable surface yet (the target node block carries only `model`; no
// winning PromptTemplate is stamped into the round/dashboard).
//
// M12 (operator-steered fork — docs/specs/m12-operator-steered-fork.md): when
// the control-plane write path lands, flip PipelineConfigEditor to mode="edit" +
// a writable PromptFieldsEditor. The edit doesn't mutate the dataset-scoped
// origin (that would change every run on the dataset) — it seeds a *fork* from
// the selected searchpoint that carries the edits, so the campaign continues
// optimizing from the steered point. Hence the "fork from a searchpoint" note.

// Demo fallback — the preview ChatPane has no real dataset behind the hero.
// Mirrors the conservative floor (model/provider frozen, no allowed-value sets).
const DEMO_LOCKS: OptimizerLocks = {
  pipeline: ["llm_only"],
  forbidden_axes: ["model", "provider"],
  nodes: { llm_only: { config: {}, param_allowed_values: {} } },
};

interface Props {
  cv: ConnectorView;
  onClose: () => void;
}

// Fronts the pipeline's primary backend node (PipelineConfigEditor picks it via
// primaryNode(locks)) — for the single llm_only target this is the clicked node.
// M12 (per-node editing of a multi-node pipeline) re-introduces an explicit
// `id` prop to scope the editor + overlay write to the selected node.
export function BackendNodeDetail({ cv, onClose }: Props) {
  const locks = cv.optimizerLocks ?? DEMO_LOCKS;
  // The lock toggle reads off the campaign policy the server already resolved
  // into forbidden_axes — single source, no second derivation.
  const lockModel = locks.forbidden_axes.includes("model");
  const startingPrompt = cv.startingPrompt;

  return (
    <div className="bnode">
      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">Pipeline, optimizer &amp; prompt</span>
          <span className="setup-preview-side">
            <span className="setup-preview-sub">
              {cv.isLive
                ? "read-only — running"
                : "read-only — to steer, stop the run and fork from a searchpoint (coming)"}
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

        <PipelineConfigEditor locks={locks} overlayBase={{}} lockModel={lockModel} mode="readonly" />

        <hr className="setup-preview-divider" />

        {/* Always render the fields — when the dataset ships no authored prompt
            the six labelled fields show empty (with their hint placeholders), so
            the operator sees the prompt structure to reason against. */}
        <PromptFieldsEditor value={startingPrompt ?? {}} demo readOnly flat />
      </section>

      <details className="new-campaign-optional">
        <summary>Review hyperparameters</summary>
        <div className="new-campaign-optional-body">
          <LockTable params={lockedParams(locks)} />
        </div>
      </details>
    </div>
  );
}

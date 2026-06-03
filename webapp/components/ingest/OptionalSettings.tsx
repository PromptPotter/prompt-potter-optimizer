"use client";

import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { SlugField } from "@/components/forms/SlugField";
import { TextField } from "@/components/forms/TextField";
import { NumberField } from "@/components/forms/NumberField";
import { OptimizerLLMField } from "./OptimizerLLMField";

// The optional, never-blocking refinements — slug, task framing, max rounds,
// optimizer LLM, sample preview. Folded under the Review-hyperparameters
// expander so the start path stays short.
export function OptionalSettings({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  return (
    <>
      <SlugField slug={draft.slug} onApply={(slug) => onApply({ slug })} />
      <TextField
        label="Task description"
        value={draft.task_description}
        placeholder="What is the model supposed to do with each row?"
        onApply={(task_description) => onApply({ task_description })}
      />
      <NumberField
        label="Max rounds"
        value={draft.max_rounds}
        min={1}
        max={100}
        onApply={(max_rounds) => onApply({ max_rounds })}
      />
      <OptimizerLLMField
        provider={draft.optimizer_provider}
        model={draft.optimizer_model}
        onApply={(optimizer_provider, optimizer_model) =>
          onApply({ optimizer_provider, optimizer_model })
        }
      />
      <details>
        <summary>Sample preview ({draft.sample_preview.length})</summary>
        <ul className="new-campaign-preview-list">
          {draft.sample_preview.map((row, i) => (
            <li key={i}>
              <code>{row.query}</code> → <code>{row.ground_truth}</code>
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}

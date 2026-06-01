"use client";

import { useState } from "react";
import type { DraftCampaignWire, DraftPatch } from "@/lib/api";
import { lockedParams } from "@/lib/optimizer-locks";
import { RecapCard } from "./RecapCard";
import { LockTable } from "./LockTable";
import { OptionalSettings } from "./OptionalSettings";
import { PipelineConfigEditor } from "./PipelineConfigEditor";
import { PromptFieldsEditor } from "./PromptFieldsEditor";

// Step 3 — one compact preview the operator reviews before launch: the
// editable pipeline/optimizer config (model lock, allowed models, thinking
// levels) and the editable starting prompt, in a single card. In demo every
// edit is local and resets on Start; otherwise edits persist to the draft.
// The footer + step nav live in DraftCommitFlow.
export function PipelinePromptStep({
  draft,
  demo,
  recapText,
  onApply,
}: {
  draft: DraftCampaignWire;
  demo: boolean;
  recapText: string;
  onApply: (patch: DraftPatch) => void;
}) {
  const [reviewing, setReviewing] = useState(false);

  return (
    <>
      <RecapCard text={recapText} />

      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">Pipeline, optimizer &amp; prompt</span>
          <span className="setup-preview-sub">
            {demo ? "demo — edits reset on Start" : "the optimizer evolves the prompt within these"}
          </span>
        </header>

        <PipelineConfigEditor draft={draft} demo={demo} onApply={onApply} />

        <hr className="setup-preview-divider" />

        <PromptFieldsEditor value={draft.starting_prompt} demo={demo} onApply={onApply} flat />
      </section>

      <details
        className="new-campaign-optional"
        open={reviewing}
        onToggle={(e) => setReviewing((e.target as HTMLDetailsElement).open)}
      >
        <summary>Review hyperparameters</summary>
        <div className="new-campaign-optional-body">
          <LockTable params={lockedParams(draft.optimizer_locks)} />
          <OptionalSettings draft={draft} onApply={onApply} />
        </div>
      </details>
    </>
  );
}

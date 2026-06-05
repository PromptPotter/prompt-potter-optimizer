"use client";

import { useState } from "react";
import {
  IngestApiError,
  postEditDraftCampaign,
  postMintCampaignFromDraft,
  postResolveOrigin,
  type DraftCampaignWire,
  type DraftPatch,
  type OriginGap,
  type OriginLastResolution,
} from "@/lib/api";
import { originReadiness, plainLanguageRecap } from "@/lib/origin-readiness";
import { TextField } from "@/components/forms/TextField";
import { ColumnMappingPicker } from "./ColumnMappingPicker";
import { OriginCheckinPanel } from "./OriginCheckinPanel";
import { PipelinePromptStep } from "./PipelinePromptStep";
import { CheckinLoadingWindow } from "./CheckinLoadingWindow";
import type { OnMinted } from "./types";

export type WizardStep = 1 | 2 | 3;

// The setup wizard over one server-held draft, as a 1-2-3:
//   1 · Your data & goal — sample count + the task context (task_description),
//       plus the AI check-in that interprets it (skipped only for a draft
//       pre-filled from an existing dataset — `draft.derived_from_dataset`).
//   2 · Map columns — which uploaded column is the input, which is the target.
//   3 · Pipeline & prompt — the locked pipeline config + the editable starting
//       prompt the check-in authored.
// `step` is owned by IngestPane so the modal header can title each step; this
// component renders the active step's body + the shared Back/Next/Start footer.
export function DraftCommitFlow({
  draft,
  step,
  onStep,
  onDraftChange,
  initialResolution,
  onClose,
  onMinted,
}: {
  draft: DraftCampaignWire;
  step: WizardStep;
  onStep: (s: WizardStep) => void;
  onDraftChange: (d: DraftCampaignWire) => void;
  // A check-in resolution run before the wizard opened (the chat file-drop) —
  // seeds the panel so its assessment/questions show without a re-click.
  initialResolution?: OriginLastResolution | null;
  onClose: () => void;
  onMinted?: OnMinted;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Server-side `origin_incomplete` gaps from a rejected mint — only appear if
  // the operator forces a commit the client gate already blocks.
  const [serverGaps, setServerGaps] = useState<OriginGap[] | null>(null);
  const [resolving, setResolving] = useState(false);
  const [lastResolution, setLastResolution] = useState<OriginLastResolution | null>(
    initialResolution ?? null,
  );
  // A draft pre-filled from an existing dataset (a demo/benchmark pick) is
  // already fully resolved, so the AI check-in has nothing to add and prompt
  // edits are throwaway. This is a per-draft fact (`derived_from_dataset`), NOT
  // the global `demo_mode_enabled` preference — a fresh upload always gets the
  // real check-in even while demo mode is on.
  const demo = draft.derived_from_dataset;

  const readiness = originReadiness(draft);
  const taskReady = draft.resolved["task_description"] === "confirmed";
  const columnsReady =
    draft.resolved["column.query"] === "confirmed" &&
    draft.resolved["column.ground_truth"] === "confirmed";

  const applyPatch = async (patch: DraftPatch) => {
    setError(null);
    setServerGaps(null);
    try {
      onDraftChange(await postEditDraftCampaign(draft.draft_id, patch));
    } catch (e) {
      setError(IngestApiError.toOperatorMessage(e));
    }
  };

  const handleResolve = async () => {
    setResolving(true);
    setError(null);
    setServerGaps(null);
    try {
      const r = await postResolveOrigin(draft.draft_id);
      onDraftChange(r.draft);
      setLastResolution(r.resolution.last_resolution ?? null);
    } catch (e) {
      setError(IngestApiError.toOperatorMessage(e));
    } finally {
      setResolving(false);
    }
  };

  const handleCommit = async () => {
    setSubmitting(true);
    setError(null);
    setServerGaps(null);
    try {
      const r = await postMintCampaignFromDraft(draft.draft_id);
      onMinted?.({ campaignId: r.campaign_id, cycleId: r.cycle_id });
      onClose();
    } catch (e) {
      if (e instanceof IngestApiError && e.errorCode === "origin_incomplete" && e.gaps) {
        setServerGaps(e.gaps);
      }
      setError(IngestApiError.toOperatorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  const body =
    step === 1 ? (
      <>
        <p>
          Parsed <strong>{draft.n_samples}</strong> rows from <code>{draft.slug}</code>. Describe
          what the prompt should do — the check-in reads this to set up the pipeline.
        </p>
        <TextField
          label="What should the prompt do?"
          value={draft.task_description}
          placeholder="e.g. Classify each support ticket into one category."
          onApply={(task_description) => applyPatch({ task_description })}
        />
        {demo ? (
          <p className="wizard-demo-note" role="note">
            Pre-filled from an existing dataset — setup is ready and the AI check-in isn&apos;t needed.
          </p>
        ) : resolving ? (
          <CheckinLoadingWindow model={draft.optimizer_model || "the check-in model"} />
        ) : (
          <OriginCheckinPanel
            draft={draft}
            resolving={resolving}
            lastResolution={lastResolution}
            onResolve={handleResolve}
            onApply={applyPatch}
          />
        )}
      </>
    ) : step === 2 ? (
      <>
        <p>
          Map your columns: which uploaded column is the <strong>input</strong> the model reads,
          and which is the <strong>target</strong> it must match.
        </p>
        <ColumnMappingPicker draft={draft} onApply={applyPatch} />
      </>
    ) : (
      <PipelinePromptStep
        draft={draft}
        demo={demo}
        recapText={lastResolution?.recap || plainLanguageRecap(draft)}
        onApply={applyPatch}
      />
    );

  const nextDisabled = step === 1 ? !taskReady : !columnsReady;

  return (
    <div className="new-campaign-body">
      <ol className="wizard-steps" aria-label={`Step ${step} of 3`}>
        {[1, 2, 3].map((n) => (
          <li
            key={n}
            className={`wizard-step${n === step ? " is-active" : ""}${n < step ? " is-done" : ""}`}
            aria-current={n === step ? "step" : undefined}
          >
            {n}
          </li>
        ))}
      </ol>

      {body}

      {serverGaps && serverGaps.length > 0 ? (
        <ul className="origin-gaps origin-gaps--blocked">
          {serverGaps.map((g) => (
            <li key={g.field}>{g.hint}</li>
          ))}
        </ul>
      ) : null}
      {error ? <p className="new-campaign-error">{error}</p> : null}

      <footer className="new-campaign-footer">
        <button
          type="button"
          className="new-campaign-cancel"
          onClick={() => (step === 1 ? onClose() : onStep((step - 1) as WizardStep))}
        >
          {step === 1 ? "Cancel" : "Back"}
        </button>
        {step < 3 ? (
          <button
            type="button"
            className="new-campaign-submit"
            disabled={nextDisabled}
            onClick={() => onStep((step + 1) as WizardStep)}
          >
            Next
          </button>
        ) : (
          <button
            type="button"
            className="new-campaign-submit"
            disabled={submitting || !readiness.complete}
            onClick={handleCommit}
          >
            {submitting ? "Starting…" : "Start campaign"}
          </button>
        )}
      </footer>
    </div>
  );
}

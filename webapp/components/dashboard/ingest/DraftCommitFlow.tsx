"use client";

import { useEffect, useState } from "react";
import {
  fetchUserSettings,
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
import { GapList } from "./GapList";
import { PipelinePromptStep } from "./PipelinePromptStep";
import { CheckinLoadingWindow } from "./CheckinLoadingWindow";
import type { OnMinted } from "./types";

export type WizardStep = 1 | 2 | 3;

// The setup wizard over one server-held draft, as a 1-2-3:
//   1 · Your data & goal — sample count + the task context (task_description),
//       plus the AI check-in that interprets it (skipped in demo mode).
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
  onClose,
  onMinted,
}: {
  draft: DraftCampaignWire;
  step: WizardStep;
  onStep: (s: WizardStep) => void;
  onDraftChange: (d: DraftCampaignWire) => void;
  onClose: () => void;
  onMinted?: OnMinted;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Server-side `origin_incomplete` gaps from a rejected mint — only appear if
  // the operator forces a commit the client gate already blocks.
  const [serverGaps, setServerGaps] = useState<OriginGap[] | null>(null);
  const [resolving, setResolving] = useState(false);
  const [lastResolution, setLastResolution] = useState<OriginLastResolution | null>(null);
  // Demo mode (Account → Preferences) skips the check-in and treats prompt
  // edits as throwaway. Defaults closed until the one-shot fetch resolves.
  const [demo, setDemo] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetchUserSettings()
      .then((s) => {
        if (!cancelled) setDemo(s.demo_mode_enabled);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

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
            Demo dataset — setup is pre-filled and the AI check-in is skipped.
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

      {serverGaps ? <GapList gaps={serverGaps} tone="blocked" /> : null}
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

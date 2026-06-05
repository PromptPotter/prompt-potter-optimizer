"use client";

import { useEffect, useRef, useState } from "react";
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
import { lockedParams } from "@/lib/optimizer-locks";
import { TextField } from "@/components/forms/TextField";
import { NumberField } from "@/components/forms/NumberField";
import { SlugField } from "@/components/forms/SlugField";
import { ColumnMappingPicker } from "./ColumnMappingPicker";
import { OriginCheckinPanel } from "./OriginCheckinPanel";
import { OptimizerLLMField } from "./OptimizerLLMField";
import { CheckinLoadingWindow } from "./CheckinLoadingWindow";
import { AllowedValuesEditor } from "@/components/dashboard/control/AllowedValuesEditor";
import { LockTable } from "@/components/dashboard/control/LockTable";
import { PromptFieldsEditor } from "@/components/dashboard/control/PromptFieldsEditor";
import type { OnMinted } from "./types";

// One-window campaign setup over a single server-held draft. The old 1-2-3
// wizard collapsed into a single scroll: state the goal → the check-in runs
// itself (no "Set up with AI" button) → map columns → review the authored
// prompt → Start. Optimizer/pipeline tunables ride an optional Advanced
// expander — they default sensibly and the operator rarely touches them.
export function DraftCommitFlow({
  draft,
  onDraftChange,
  initialResolution,
  onClose,
  onMinted,
}: {
  draft: DraftCampaignWire;
  onDraftChange: (d: DraftCampaignWire) => void;
  // A check-in resolution run before the wizard opened (the chat file-drop) —
  // seeds the panel so its assessment/questions show without re-resolving.
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
  // edits are throwaway. Per-draft fact (`derived_from_dataset`), NOT the
  // global `demo_mode_enabled` preference.
  const demo = draft.derived_from_dataset;

  const readiness = originReadiness(draft);
  const recapText = lastResolution?.recap || plainLanguageRecap(draft);

  const applyPatch = async (patch: DraftPatch) => {
    setError(null);
    setServerGaps(null);
    try {
      onDraftChange(await postEditDraftCampaign(draft.draft_id, patch));
    } catch (e) {
      setError(IngestApiError.toOperatorMessage(e));
    }
  };

  const hasContext = draft.task_description.trim().length > 0;

  // Auto-run the check-in once per draft — this replaces the old "Set up with
  // AI" button. It waits for the operator to provide context (the task): with
  // no task there is nothing to configure the origin from, so we ask for it
  // first rather than burning a call. Skipped for a demo draft (already
  // resolved) and a chat-seeded resolution (initialResolution).
  const autoResolved = useRef<string | null>(initialResolution ? draft.draft_id : null);
  useEffect(() => {
    if (demo || !hasContext || autoResolved.current === draft.draft_id) return;
    autoResolved.current = draft.draft_id;
    let cancelled = false;
    setResolving(true);
    setError(null);
    postResolveOrigin(draft.draft_id)
      .then((r) => {
        if (cancelled) return;
        onDraftChange(r.draft);
        setLastResolution(r.resolution.last_resolution ?? null);
      })
      .catch((e) => {
        if (!cancelled) setError(IngestApiError.toOperatorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setResolving(false);
      });
    return () => {
      cancelled = true;
    };
  }, [draft.draft_id, hasContext, demo, onDraftChange]);

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

  return (
    <div className="new-campaign-body">
      <p>
        Parsed <strong>{draft.n_samples}</strong> rows from <code>{draft.slug}</code>. Describe
        what the prompt should do — the check-in reads this to set up the campaign.
      </p>

      <TextField
        label="What should the prompt do?"
        value={draft.task_description}
        placeholder="e.g. Classify each support ticket into one category."
        onApply={(task_description) => applyPatch({ task_description })}
      />

      {demo ? (
        <p className="wizard-demo-note" role="note">
          Pre-filled from an existing dataset — setup is ready and the AI check-in isn&apos;t
          needed.
        </p>
      ) : resolving ? (
        <CheckinLoadingWindow model={draft.optimizer_model || "the check-in model"} />
      ) : !hasContext && !lastResolution ? (
        <p className="wizard-demo-note" role="note">
          Describe the task in one message above — as much as you can about what the model should
          do with each row. The check-in reads it once and fills in the rest.
        </p>
      ) : (
        <OriginCheckinPanel draft={draft} lastResolution={lastResolution} onApply={applyPatch} />
      )}

      <ColumnMappingPicker draft={draft} onApply={applyPatch} />

      {draft.sample_preview.length > 0 ? (
        <details className="new-campaign-preview" open>
          <summary>Sample preview ({draft.sample_preview.length})</summary>
          <ul className="new-campaign-preview-list">
            {draft.sample_preview.map((row, i) => (
              <li key={i}>
                <code>{row.query}</code> → <code>{row.ground_truth}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="origin-recap" role="status">
        <span className="origin-recap-tick" aria-hidden="true">
          ✓
        </span>
        <p>{recapText}</p>
      </div>

      <section className="setup-preview">
        <header className="setup-preview-head">
          <span className="setup-preview-title">Starting prompt</span>
          <span className="setup-preview-sub">
            {demo ? "demo — edits reset on Start" : "the optimizer evolves this from here"}
          </span>
        </header>
        <PromptFieldsEditor value={draft.starting_prompt} demo={demo} onApply={applyPatch} flat />
      </section>

      <details className="new-campaign-optional">
        <summary>Optimizer &amp; pipeline (optional)</summary>
        <div className="new-campaign-optional-body">
          <AllowedValuesEditor
            locks={draft.optimizer_locks}
            overlayBase={draft.pipeline_overlay}
            lockModel={draft.lock_model}
            mode={demo ? "demo" : "edit"}
            onApply={applyPatch}
          />
          <OptimizerLLMField
            provider={draft.optimizer_provider}
            model={draft.optimizer_model}
            onApply={(optimizer_provider, optimizer_model) =>
              applyPatch({ optimizer_provider, optimizer_model })
            }
          />
          <NumberField
            label="Max rounds"
            value={draft.max_rounds}
            min={1}
            max={100}
            onApply={(max_rounds) => applyPatch({ max_rounds })}
          />
          <SlugField slug={draft.slug} onApply={(slug) => applyPatch({ slug })} />
          <LockTable params={lockedParams(draft.optimizer_locks)} />
        </div>
      </details>

      {serverGaps && serverGaps.length > 0 ? (
        <ul className="origin-gaps origin-gaps--blocked">
          {serverGaps.map((g) => (
            <li key={g.field}>{g.hint}</li>
          ))}
        </ul>
      ) : null}
      {error ? <p className="new-campaign-error">{error}</p> : null}

      <footer className="new-campaign-footer">
        <button type="button" className="new-campaign-cancel" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="new-campaign-submit"
          disabled={submitting || resolving || !readiness.complete}
          onClick={handleCommit}
        >
          {submitting ? "Starting…" : "Start campaign"}
        </button>
      </footer>
    </div>
  );
}

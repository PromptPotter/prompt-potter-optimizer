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
import { GapList } from "./GapList";
import { PermissionReviewStep } from "./PermissionReviewStep";
import type { OnMinted } from "./types";

// ----- Draft commit (used by ChatIngestFlow after a successful upload) -----

export function DraftCommitFlow({
  draft,
  onDraftChange,
  onClose,
  onMinted,
}: {
  draft: DraftCampaignWire;
  onDraftChange: (d: DraftCampaignWire) => void;
  onClose: () => void;
  onMinted?: OnMinted;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Server-side `origin_incomplete` gaps from a rejected mint. Distinct from
  // the proactively-derived gaps shown in the required tier: these only
  // appear if the operator forces a commit the client gate already blocks.
  const [serverGaps, setServerGaps] = useState<OriginGap[] | null>(null);
  // One origin-resolver turn in flight + its last output (assessment,
  // operator questions, ready-turn recap).
  const [resolving, setResolving] = useState(false);
  const [lastResolution, setLastResolution] = useState<OriginLastResolution | null>(null);

  const readiness = originReadiness(draft);

  const applyPatch = async (patch: DraftPatch) => {
    setError(null);
    setServerGaps(null);
    try {
      const next = await postEditDraftCampaign(draft.draft_id, patch);
      onDraftChange(next);
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

  // Two phases over one server-held draft: the origin setup (map columns + AI
  // check-in) gates on `readiness.complete`; once ready, the permission step
  // (pipeline + optimizer locks) is the "another window" before mint.
  if (!readiness.complete) {
    return (
      <div className="new-campaign-body">
        <p>
          Parsed <strong>{draft.n_samples}</strong> rows from{" "}
          <code>{draft.slug}</code>. First, map your columns — then let the
          check-in finish the setup.
        </p>

        {/* Required tier — the column mapping the mint gate blocks on. */}
        <ColumnMappingPicker draft={draft} onApply={applyPatch} />

        {/* Origin setup-in-progress — the closed-set fields with provenance, and
            the AI resolver that proposes the task framing + refinements. */}
        <OriginCheckinPanel
          draft={draft}
          resolving={resolving}
          lastResolution={lastResolution}
          onResolve={handleResolve}
          onApply={applyPatch}
        />

        {/* Task framing is the one required field with no default — let the
            operator type it directly, not only via "Set up with AI". */}
        <TextField
          label="Describe what the prompt should do"
          value={draft.task_description}
          placeholder="e.g. Classify each support ticket into one category."
          onApply={(task_description) => applyPatch({ task_description })}
        />

        <GapList gaps={readiness.gaps} tone="pending" />

        {error ? <p className="new-campaign-error">{error}</p> : null}
        <footer className="new-campaign-footer">
          <button type="button" className="new-campaign-cancel" onClick={onClose}>
            Cancel
          </button>
        </footer>
      </div>
    );
  }

  return (
    <PermissionReviewStep
      draft={draft}
      recapText={lastResolution?.recap || plainLanguageRecap(draft)}
      submitting={submitting}
      serverGaps={serverGaps}
      error={error}
      onApply={applyPatch}
      onStart={handleCommit}
      onClose={onClose}
    />
  );
}

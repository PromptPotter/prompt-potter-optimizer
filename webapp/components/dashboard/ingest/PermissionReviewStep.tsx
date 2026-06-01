"use client";

import { useState } from "react";
import type { DraftCampaignWire, DraftPatch, OriginGap } from "@/lib/api";
import { lockedParams, primaryNode, thinkingLadder } from "@/lib/optimizer-locks";
import { RecapCard } from "./RecapCard";
import { LockTable } from "./LockTable";
import { OptionalSettings } from "./OptionalSettings";
import { GapList } from "./GapList";

// The "another window" before mint. Setup is ready; this surfaces what the
// optimizer may and may NOT permute on the backend pipeline — the locked model,
// the thinking floor (`low`) with `medium`/`high` crossed out — so the operator
// starts knowing the rails, or opens "Review hyperparameters" to see the full
// per-node lock list (+ the optional settings) inline.
export function PermissionReviewStep({
  draft,
  recapText,
  submitting,
  serverGaps,
  error,
  onApply,
  onStart,
  onClose,
}: {
  draft: DraftCampaignWire;
  recapText: string;
  submitting: boolean;
  serverGaps: OriginGap[] | null;
  error: string | null;
  onApply: (patch: DraftPatch) => void;
  onStart: () => void;
  onClose: () => void;
}) {
  const [reviewing, setReviewing] = useState(false);
  const locks = draft.optimizer_locks;
  const node = primaryNode(locks);
  const thinking = thinkingLadder(locks, node);
  const modelLocked = locks.forbidden_axes.includes("model");

  return (
    <div className="new-campaign-body">
      <RecapCard text={recapText} />

      <section className="opt-locks">
        <header className="origin-columns-head">Pipeline &amp; optimizer permissions</header>
        <p className="opt-locks-lead">
          The optimizer evolves your prompt — but it can&rsquo;t touch these. They
          stay fixed for the whole campaign.
        </p>

        <div className="opt-locks-row">
          <span className="opt-locks-label">Pipeline</span>
          <span className="opt-locks-pipeline">
            {locks.pipeline.length > 0
              ? locks.pipeline.map((step) => (
                  <span key={step} className="opt-locks-chip">
                    {step}
                  </span>
                ))
              : "backend default"}
          </span>
        </div>

        {modelLocked ? (
          <div className="opt-locks-row">
            <span className="opt-locks-label">Model</span>
            <span className="opt-locks-locked">
              <span className="opt-locks-lock" aria-hidden="true">
                🔒
              </span>
              Locked — the optimizer can&rsquo;t change the model.
            </span>
          </div>
        ) : null}

        <div className="opt-locks-row">
          <span className="opt-locks-label">Thinking</span>
          <span className="opt-locks-ladder">
            {thinking.options.map((opt) => (
              <span
                key={opt.key}
                className={`opt-locks-level${opt.active ? " is-active" : ""}${
                  opt.allowed ? "" : " is-crossed"
                }`}
                title={
                  opt.allowed
                    ? opt.active
                      ? "Current level"
                      : "Allowed"
                    : "Optimizer locked out of this level"
                }
              >
                {opt.key}
              </span>
            ))}
          </span>
        </div>
        <small className="opt-locks-hint">
          Crossed-out levels are off-limits to the optimizer — it can&rsquo;t
          escalate thinking beyond <strong>{thinking.value ?? "the floor"}</strong>.
        </small>
      </section>

      <details
        className="new-campaign-optional"
        open={reviewing}
        onToggle={(e) => setReviewing((e.target as HTMLDetailsElement).open)}
      >
        <summary>Review hyperparameters</summary>
        <div className="new-campaign-optional-body">
          <LockTable params={lockedParams(locks)} />
          <OptionalSettings draft={draft} onApply={onApply} />
        </div>
      </details>

      {serverGaps ? <GapList gaps={serverGaps} tone="blocked" /> : null}
      {error ? <p className="new-campaign-error">{error}</p> : null}
      <footer className="new-campaign-footer">
        <button type="button" className="new-campaign-cancel" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="new-campaign-submit"
          disabled={submitting}
          onClick={onStart}
        >
          {submitting ? "Starting…" : "Start campaign"}
        </button>
      </footer>
    </div>
  );
}

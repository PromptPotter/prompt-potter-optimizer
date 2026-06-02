"use client";

import { useRef, useState } from "react";
import { postCreateFork, type ForkSeed, type LimitOverrides } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { useRoundFile } from "@/lib/useRoundFile";
import { candidateSearchPoint } from "@/lib/derivations/candidateSearchPoint";
import { forkReconcileDefaults, limitOverridesFromDefaults } from "@/lib/derivations/forkReconcile";
import type { SelectedCandidate } from "@/lib/types/selection";
import type { DashboardSnapshot } from "@/lib/poll";
import { PromptFieldsEditor } from "./PromptFieldsEditor";
import { LimitReconcile } from "./LimitReconcile";

// The one operator-steered fork flow (decision H): the operator has selected a
// searchpoint; this seeds its EVOLVED prompt + node-config from the round file
// (decision F — rides `useRoundFile`, no new endpoint), lets them edit the
// prompt + reconcile run limits, then mints a fork tagged `operator_steered`
// (seed present) rooted at that candidate. The candidate's `pipeline_overlay`
// (its `nodes.*.config` delta) rides along verbatim — it's shown read-only and
// merged onto the dataset overlay at fork bootstrap (seed > dataset). Editing
// those config VALUES needs a value-editor matching the `pipeline_params`
// shape (NOT the ingest locks editor, which writes `param_allowed_values`); it
// is the one remaining steer follow-up.
//
// Shared by `ScoringInspector` (candidate drill-in). Self-contained: it owns
// its round-file fetch + the fork write, so any caller that has the selected
// candidate + the dashboard snapshot can mount it.

function overlayRows(overlay: Record<string, unknown>): { node: string; param: string; value: string }[] {
  const rows: { node: string; param: string; value: string }[] = [];
  for (const [node, cfg] of Object.entries(overlay)) {
    if (cfg && typeof cfg === "object" && !Array.isArray(cfg)) {
      for (const [param, value] of Object.entries(cfg as Record<string, unknown>)) {
        rows.push({ node, param, value: typeof value === "object" ? JSON.stringify(value) : String(value) });
      }
    }
  }
  return rows;
}

export function SteerForkPanel({
  campaignId,
  cycleId,
  candidate,
  dash,
  onDone,
  onCancel,
}: {
  campaignId: string | null;
  cycleId: string | null;
  candidate: SelectedCandidate;
  dash: DashboardSnapshot | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { doc } = useRoundFile(campaignId, cycleId, candidate.round);
  const seed = candidateSearchPoint(doc, candidate.candidate_id);
  const seedPrompt = seed?.starting_prompt ?? {};
  const overlay = seed?.pipeline_overlay ?? {};

  // Captured working copies, read at confirm. Refs (not state) so a textarea
  // blur that fires immediately before the Confirm click is already reflected
  // — no stale-state race. `editedPrompt` null = operator never touched it, so
  // confirm sends the loaded seed prompt as-is.
  const editedPrompt = useRef<Record<string, unknown> | null>(null);
  // Seed with the pre-filled "remaining" defaults so confirming an untouched
  // reconcile dialog forks with the SHOWN ceilings — not a silent inherit of
  // the parent's full budget. `LimitReconcile.onChange` overwrites on edit.
  const limits = useRef<LimitOverrides>(limitOverridesFromDefaults(forkReconcileDefaults(dash)));

  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const rows = overlayRows(overlay);

  const confirm = async () => {
    if (!campaignId || !cycleId) return;
    setPending(true);
    setErr(null);
    const forkSeed: ForkSeed = {
      starting_prompt: editedPrompt.current ?? seedPrompt,
      pipeline_overlay: overlay,
      limit_overrides: limits.current,
    };
    try {
      await postCreateFork(campaignId, cycleId, candidate.round, candidate.candidate_id, {
        seed: forkSeed,
      });
      bumpRevalidation();
      onDone();
    } catch (e) {
      setErr((e as Error).message);
      setPending(false);
    }
  };

  return (
    <div className="steer-fork">
      <div className="steer-fork-head">
        <span className="steer-fork-title">
          Steer &amp; fork · R{candidate.round}.{candidate.candidate_id}
        </span>
        <span className="steer-fork-sub">edit this searchpoint, then continue optimizing from it</span>
      </div>

      {!seed && (
        <p className="steer-fork-note" role="note">
          The round file for R{candidate.round} isn&apos;t on disk yet — start
          from the fields below (they seed the fork&apos;s origin prompt).
        </p>
      )}

      <PromptFieldsEditor
        value={seedPrompt}
        demo={false}
        onApply={(p) => {
          editedPrompt.current = p.starting_prompt ?? {};
        }}
        flat
      />

      <div className="steer-fork-overlay">
        <span className="steer-fork-overlay-title">Node config (carried from this searchpoint)</span>
        {rows.length > 0 ? (
          <ul className="steer-fork-overlay-list">
            {rows.map((r) => (
              <li key={`${r.node}.${r.param}`} className="steer-fork-overlay-row">
                <code>{r.node}.{r.param}</code>
                <span>{r.value}</span>
              </li>
            ))}
          </ul>
        ) : (
          <small className="steer-fork-overlay-empty">inherits the dataset config</small>
        )}
      </div>

      <LimitReconcile dash={dash} onChange={(l) => (limits.current = l)} />

      {err && <span className="steer-fork-err" role="alert">fork: {err}</span>}

      <div className="steer-fork-actions">
        <button type="button" className="steer-fork-cancel" onClick={onCancel} disabled={pending}>
          Cancel
        </button>
        <button
          type="button"
          className="steer-fork-confirm"
          onClick={() => void confirm()}
          disabled={pending}
          title="Mint a fork rooted at this searchpoint, carrying your edits. Tagged operator_steered in lineage."
        >
          {pending ? "Forking…" : "Confirm steered fork"}
        </button>
      </div>
    </div>
  );
}

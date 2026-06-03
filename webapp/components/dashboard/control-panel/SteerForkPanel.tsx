"use client";

import { useRef, useState } from "react";
import { postForkCycle, postStopCycle, type ForkSeed, type LimitOverrides } from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { useRoundFile } from "@/lib/hooks/useRoundFile";
import { useConnector } from "@/lib/hooks/useConnector";
import { useAuth } from "@/lib/auth-context";
import { candidateSearchPoint } from "@/lib/derivations/candidateSearchPoint";
import { forkReconcileDefaults, limitOverridesFromDefaults } from "@/lib/derivations/forkReconcile";
import type { SelectedCandidate } from "@/lib/types/selection";
import type { DashboardSnapshot } from "@/lib/poll";
import { PromptFieldsEditor } from "./PromptFieldsEditor";
import { NodeConfigEditor } from "./NodeConfigEditor";
import { NodeOutputSchemaView } from "./NodeOutputSchemaView";
import { LimitReconcile } from "./LimitReconcile";

// The one operator-steered fork flow (decision H): the operator has selected a
// searchpoint; this seeds its EVOLVED prompt + node-config from the round file
// (decision F — rides `useRoundFile`, no new endpoint), lets them edit the
// prompt, the node-config VALUES, and reconcile run limits, then mints a fork
// tagged `operator_steered` (seed present) rooted at that candidate — stamped
// with the operator who steered it. When the parent is still running, the
// confirm stops it first (the steer IS a "stop → redirect" act).
//
// Shared by `ScoringInspector` (candidate drill-in) + `BackendNodeDetail`
// (node click on a stopped cycle). Self-contained: it owns its round-file
// fetch and the fork write, and reads the shared connector view via
// `useConnector()`, so any caller under `ConnectorProvider` can mount it.

export function SteerForkPanel({
  campaignId,
  cycleId,
  candidate,
  dash,
  isLive,
  onDone,
  onCancel,
}: {
  campaignId: string | null;
  cycleId: string | null;
  candidate: SelectedCandidate;
  dash: DashboardSnapshot | null;
  // When the parent is running, confirm stops it before forking.
  isLive: boolean;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { doc } = useRoundFile(campaignId, cycleId, candidate.round);
  const cv = useConnector();
  const { me } = useAuth();
  const seed = candidateSearchPoint(doc, candidate.candidate_id);
  const seedPrompt = seed?.starting_prompt ?? {};
  const overlay = seed?.pipeline_overlay ?? {};

  // Captured working copies, read at confirm. Refs (not state) so a textarea
  // blur that fires immediately before the Confirm click is already reflected
  // — no stale-state race. `null` = operator never touched it, so confirm uses
  // the loaded seed value as-is (handles the async round-file load too).
  const editedPrompt = useRef<Record<string, unknown> | null>(null);
  const editedOverlay = useRef<Record<string, Record<string, unknown>> | null>(null);
  // Seed with the pre-filled "remaining" defaults so confirming an untouched
  // reconcile dialog forks with the SHOWN ceilings — not a silent inherit of
  // the parent's full budget. `LimitReconcile.onChange` overwrites on edit.
  const limits = useRef<LimitOverrides>(limitOverridesFromDefaults(forkReconcileDefaults(dash)));

  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const steeredBy = me?.name || me?.email || me?.user_id || undefined;

  const confirm = async () => {
    if (!campaignId || !cycleId) return;
    setPending(true);
    setErr(null);
    const forkSeed: ForkSeed = {
      starting_prompt: editedPrompt.current ?? seedPrompt,
      pipeline_overlay: editedOverlay.current ?? overlay,
      limit_overrides: limits.current,
    };
    try {
      // The steer redirects the run — stop the live parent first so the
      // operator's next resume picks up the fork without racing the loop.
      if (isLive) await postStopCycle(campaignId, cycleId);
      await postForkCycle(campaignId, cycleId, candidate.round, candidate.candidate_id, {
        seed: forkSeed,
        steeredBy,
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
      <p className="steer-fork-sub">
        Review or edit this searchpoint&apos;s evolved prompt, model &amp; parameters,
        then fork-continue optimizing from it. Edits are optional.
      </p>

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

      <NodeConfigEditor
        schema={cv.nodeConfigSchema}
        seedOverlay={overlay}
        onChange={(o) => {
          editedOverlay.current = o;
        }}
      />

      <NodeOutputSchemaView schema={cv.nodeOutputSchema} />

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
          {pending ? "Forking…" : isLive ? "Stop & steer-fork" : "Confirm steered fork"}
        </button>
      </div>
    </div>
  );
}

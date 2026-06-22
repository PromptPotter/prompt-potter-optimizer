"use client";

import { useRef, useState } from "react";
import {
  postForkCycle,
  postPauseCycle,
  type DraftPatch,
  type LimitOverrides,
  type OperatorForkOverride,
} from "@/lib/api";
import { bumpRevalidation } from "@/lib/revalidate";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useAuth } from "@/lib/auth-context";
import {
  candidateSearchPoint,
  liveCandidateSearchPoint,
  forkReconcileDefaults,
  limitOverridesFromDefaults,
  searchPoint,
} from "@/lib/derivations";
import type { SelectedCandidate } from "@/lib/types";
import { NodeSurface } from "@/components/dashboard/pipeline/NodeSurface";
import { NodeConfigEditor } from "./NodeConfigEditor";
import { LimitReconcile } from "./LimitReconcile";

// The one operator-steered fork flow (decision H): the operator has selected a
// searchpoint; this seeds its EVOLVED prompt + node-config, lets them edit the
// prompt, the node-config VALUES, and reconcile run limits, then mints a fork
// tagged `operator_steered` (seed present) rooted at that candidate — stamped
// with the operator who steered it. When the parent is still running, the
// confirm stops it first (the steer IS a "stop → redirect" act).
//
// Seed source follows the no-stitch rule via `useRoundSource`: a *completed*
// round's candidate seeds from `round_NNNN.json::candidate_scores`; an
// *in-flight* round's candidate seeds from `dashboard.json`'s live l1_score
// input (so you can steer-fork a still-running candidate without 404ing on its
// not-yet-written round file).
//
// Shared by `ScoringInspector` (candidate drill-in) + `BackendNodeDetail`
// (node click on a stopped cycle). Self-contained: it owns its source pick and
// the fork write, and reads the shared connector view via `useConnector()`, so
// any caller under `ConnectorProvider` can mount it.

export function SteerForkPanel({
  candidate,
  onDone,
  onCancel,
}: {
  candidate: SelectedCandidate;
  onDone: () => void;
  onCancel: () => void;
}) {
  // Self-sourced live snapshot + ids + connector view. `isLive` (is the parent
  // cycle running — confirm stops it before forking) is the connector's
  // liveness; `roundIsLive` (is `candidate.round` the in-flight round) is
  // distinct — the live round's seed comes from `dashboard.json`, a completed
  // round's from its round file.
  const { dash } = useDashboard();
  const { campaignId, cycleId } = useWorkspace();
  const cv = useConnector();
  const isLive = cv.isLive;
  const { isLive: roundIsLive, doc } = useRoundSource(
    campaignId,
    cycleId,
    candidate.round,
    dash,
  );
  const { me } = useAuth();
  const seed = roundIsLive
    ? liveCandidateSearchPoint(dash, candidate.candidate_id)
    : candidateSearchPoint(doc, candidate.candidate_id);
  const seedPrompt = seed?.origin_prompt_fields ?? {};
  const overlay = seed?.pipeline_overlay ?? {};

  // Captured working copies, read at confirm. Refs (not state) so a textarea
  // blur that fires immediately before the Confirm click is already reflected
  // — no stale-state race. `null` = operator never touched it, so confirm uses
  // the loaded seed value as-is (handles the async round-file load too).
  const editedPrompt = useRef<Record<string, unknown> | null>(null);
  const editedOverlay = useRef<Record<string, Record<string, unknown>> | null>(null);
  // Per-node search-space lock edits, keyed by node → NodeSearchNarrowing
  // (`{param_keys, param_allowed_values}`). Each per-node lock editor emits the
  // whole-overlay patch; we keep only its `optimizer` block. Rides
  // `OperatorForkOverride.optimizer_narrowing` → the fork seed (cycle-level lock
  // override of the campaign's mint-time narrowing). Empty = inherit unchanged.
  const editedNarrowing = useRef<Record<string, unknown>>({});
  const captureLocks = (nodeId: string, patch: DraftPatch) => {
    const overlay = (patch.pipeline_overlay ?? {}) as Record<string, { optimizer?: unknown }>;
    const opt = overlay[nodeId]?.optimizer;
    if (opt) editedNarrowing.current = { ...editedNarrowing.current, [nodeId]: opt };
  };
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
    const forkSeed: OperatorForkOverride = {
      origin_prompt_fields: editedPrompt.current ?? seedPrompt,
      pipeline_overlay: editedOverlay.current ?? overlay,
      limit_overrides: limits.current,
      ...(Object.keys(editedNarrowing.current).length > 0
        ? { optimizer_narrowing: editedNarrowing.current }
        : {}),
    };
    try {
      // The steer redirects the run — pause the live parent first (the worker
      // exits cleanly) so the fork launch doesn't race the parent's loop.
      if (isLive) await postPauseCycle(campaignId, cycleId);
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
          This searchpoint isn&apos;t loaded yet — start from the fields below
          (they seed the fork&apos;s origin prompt).
        </p>
      )}

      <NodeSurface
        node={null}
        point={searchPoint(seedPrompt, overlay)}
        configSeed={overlay}
        schema={cv.nodeConfigSchema}
        outputSchema={cv.nodeOutputSchema}
        mode="values"
        flat
        onApply={(p) => {
          editedPrompt.current = p.origin_prompt_fields ?? {};
        }}
        onConfigChange={(o) => {
          editedOverlay.current = o;
        }}
      />

      {/* Per-node search-space LOCK editor — which params the optimizer may move on
          the fork. Seeded from `{}` so each row reflects the served (campaign-narrowed)
          schema's tunability; edits ride `optimizer_narrowing` on the fork seed. The
          model lock is the campaign-level knob (set at start), so it's not shown here. */}
      {Object.keys(cv.nodeConfigSchema ?? {}).length > 0 ? (
        <div className="steer-fork-locks">
          <p className="steer-fork-sub">
            Lock or unlock what the optimizer may tune on this fork — 🔒 holds a param at
            its value, 🔓 lets the optimizer move it.
          </p>
          {Object.keys(cv.nodeConfigSchema ?? {}).map((nodeId) => (
            <NodeConfigEditor
              key={nodeId}
              mode="search-space"
              locksOnly
              schema={cv.nodeConfigSchema}
              node={nodeId}
              seedOverlay={{}}
              onApply={(patch) => captureLocks(nodeId, patch)}
            />
          ))}
        </div>
      ) : null}

      <LimitReconcile onChange={(l) => (limits.current = l)} />

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

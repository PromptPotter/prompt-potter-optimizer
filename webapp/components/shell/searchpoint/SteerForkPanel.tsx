"use client";

import { useRef, useState } from "react";
import {
  postForkCycle,
  postPauseCycle,
  type DraftPatch,
  type RunLimitOverrides,
  type OperatorForkOverride,
} from "@/lib/api";
import type { NodeConfigParam, NodeOutputSchema, NodeSearchNarrowing } from "@/lib/api/types";
import { bumpRevalidation } from "@/lib/revalidate";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { useAuth } from "@/lib/auth-context";
import type { CyclePath } from "@/lib/ids";
import type { DashboardSnapshot } from "@/lib/poll";
import {
  candidateSearchPoint,
  liveCandidateSearchPoint,
  forkReconcileDefaults,
  configOverridesFromDefaults,
  isWidgetParam,
  overlaySetsModelOutsideAllowed,
  searchPoint,
} from "@/lib/derivations";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchCampaignDetail } from "@/lib/api";
import type { SelectedCandidate } from "@/lib/types";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { NodeConfigEditor } from "@/components/shell/node-surface/NodeConfigEditor";
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
// Opened by `SteerForkAction`, which both hosts of the searchpoint drill-in mount. It owns its
// source pick and the fork write; everything that varies with WHICH searchpoint is being steered
// arrives as a prop.
//
// **The ADDRESS is the caller's.** Read off the workspace instead, this panel could only ever
// steer the cycle the browser happened to be viewing — which is exactly the campaign a Compare
// channel is NOT on. The pipeline SCHEMA is the caller's for the same reason: it is per-dataset,
// and the app's connector view answers for the viewed one.

export function SteerForkPanel({
  candidate,
  path,
  dash,
  parentIsLive,
  schema,
  outputSchema,
  onDone,
  onCancel,
}: {
  candidate: SelectedCandidate;
  // The searchpoint's cycle — which the fork verb addresses AND whose round file seeds the
  // editors. ONE address, because `SteerForkAction` refuses anything below the top level:
  // `ForkCyclePayload` carries no `descend`, so a point whose round file lives in an `.inner/`
  // sandbox is one this command cannot name at all, and the two could only differ there.
  path: CyclePath;
  // The live snapshot for `pointPath`'s cycle, or `null` where this browser holds no stream for
  // it — exactly one cycle streams (`webapp/CLAUDE.md` § Polling shape), so a caller reading some
  // other branch has none and the seed comes from the round file, which is the only honest source.
  dash: DashboardSnapshot | null;
  // Is the parent CYCLE running — confirm stops it before forking. Distinct from `roundIsLive`
  // below, which asks whether `candidate.round` is the in-flight one.
  parentIsLive: boolean;
  // The point's OWN dataset's pipeline. Per-dataset, so it cannot be self-sourced: the app-level
  // connector view answers for whichever campaign is being VIEWED, which would seed these editors
  // from the wrong pipeline for any point outside it.
  schema: Record<string, NodeConfigParam[]> | null;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const hop = path.at(0);
  const campaignId = hop?.campaignId ?? "";
  const cycleId = hop?.cycleId ?? "";
  const isLive = parentIsLive;
  const { isLive: roundIsLive, doc } = useRoundSource(path, candidate.round, dash);
  const { me } = useAuth();
  const seed = roundIsLive
    ? liveCandidateSearchPoint(dash, candidate.candidate_id)
    : candidateSearchPoint(doc, candidate.candidate_id);
  const seedPrompt = seed?.origin_prompt_fields ?? {};
  const overlay = seed?.pipeline_overlay ?? {};
  // Nodes with a lockable param — a param this editor can DRAW that isn't the model
  // (model/provider are always optimizer-locked, never a per-node lock). The served
  // list also carries the prompt + nested params, which have no widget, so the
  // widget filter is what keeps a prompt-only pipeline (pp-self) hiding the lock
  // block entirely rather than printing a "no configurable params" box per node.
  const lockableNodes = Object.entries(schema ?? {})
    .filter(([, params]) => params.some((p) => isWidgetParam(p) && p.kind !== "model"))
    .map(([nodeId]) => nodeId);
  // The origin's declared model allow-list (`CampaignConfig.allowed_models`). Absent /
  // empty in the snapshot = nothing sanctioned = the restrictive default, so ANY model
  // steer taints — matching the server gate (`overlay_sets_model_outside_allowed`).
  const { data: detail } = useFetch(
    campaignId ? (signal) => fetchCampaignDetail(campaignId, signal) : null,
    [campaignId],
  );
  const allowedModels = (detail?.config.allowed_models as string[] | undefined) ?? [];
  // Whether the acting operator may steer a locked axis at all. Steering model/provider
  // outside the allow-list is the ADR-0005 babysit act, gated server-side on
  // `campaign.babysit` (404 without); the client reflects it so a principal who lacks
  // the cap sees the row read-only rather than a 404 on confirm. Owners hold every cap.
  const canBabysit = !!me?.capabilities?.includes("campaign.babysit");
  // Live-reactive copy of the picked overlay (the ref below is read at confirm; this
  // drives the warning, which must react to the picked model AND the async allow-list).
  // `null` = untouched → fall back to the seed overlay as it loads.
  const [pickedOverlay, setPickedOverlay] = useState<Record<
    string,
    Record<string, unknown>
  > | null>(null);
  // Warn only when the picked model is OUTSIDE what the origin sanctioned — a clean
  // steer to a sanctioned model shows nothing (the shipped allow-list gate, not the
  // old "any locked axis exists" blanket).
  const steersDisallowedModel = overlaySetsModelOutsideAllowed(
    pickedOverlay ?? overlay,
    allowedModels,
  );

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
  const editedNarrowing = useRef<Record<string, NodeSearchNarrowing>>({});
  const captureLocks = (nodeId: string, patch: DraftPatch) => {
    const overlay = (patch.pipeline_overlay ?? {}) as Record<
      string,
      { optimizer?: NodeSearchNarrowing }
    >;
    const opt = overlay[nodeId]?.optimizer;
    if (opt) editedNarrowing.current = { ...editedNarrowing.current, [nodeId]: opt };
  };
  // Seed with the pre-filled "remaining" defaults so confirming an untouched
  // reconcile dialog forks with the SHOWN ceilings — not a silent inherit of
  // the parent's full budget. `LimitReconcile.onChange` overwrites on edit.
  const limits = useRef<RunLimitOverrides>(
    configOverridesFromDefaults(forkReconcileDefaults(dash)),
  );

  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const steeredBy = me?.name || me?.email || me?.user_id || undefined;

  const confirm = async () => {
    if (!campaignId || !cycleId) return;
    setPending(true);
    setErr(null);
    const forkSeed: OperatorForkOverride = {
      origin_prompt_fields: editedPrompt.current ?? seedPrompt,
      // The overlay rides verbatim. A model/provider value in it is a locked-axis
      // edit: the backend requires `campaign.babysit` (404 without) and stamps the
      // branch grade C. A non-cap operator can't produce one — the row is read-only.
      pipeline_overlay: editedOverlay.current ?? overlay,
      config_overrides: limits.current,
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

      {/* Babysit warning. Shown only when the picked model/provider is OUTSIDE the
          origin's allowed_models (a sanctioned model is a clean steer) and the operator
          holds the cap. Steering outside the allow-list marks the branch grade C. */}
      {steersDisallowedModel && canBabysit ? (
        <div className="steer-fork-babysit">
          <p className="steer-fork-babysit-warn" role="note">
            This model isn&apos;t in the origin&apos;s allowed models
            {allowedModels.length > 0 ? ` (${allowedModels.join(", ")})` : ""}. Steering to
            it marks this branch — and every round after it — operator-babysat (grade C):
            excluded from clean comparison, origin reuse, and the L4 rollup. The
            measurement is still recorded; it just isn&apos;t a clean one. Pick a sanctioned
            model to keep a clean branch.
          </p>
        </div>
      ) : null}

      <NodeSurface
        node={null}
        point={searchPoint(seedPrompt, overlay)}
        configSeed={overlay}
        schema={schema}
        outputSchema={outputSchema}
        mode="values"
        babysitEditable={canBabysit}
        onApply={(p) => {
          editedPrompt.current = p.origin_prompt_fields ?? {};
        }}
        onConfigChange={(o) => {
          editedOverlay.current = o;
          setPickedOverlay(o);
        }}
      />

      {/* Per-node search-space LOCK editor — which params the optimizer may move on
          the fork. Seeded from `{}` so each row reflects the served (campaign-narrowed)
          schema's tunability; edits ride `optimizer_narrowing` on the fork seed. The
          model rides the node surface as a steerable value (its optimizer-lock is the
          fork-level axis toggle), so a node whose ONLY served param is the model has
          nothing to lock here — render lock rows only for nodes that carry a lockable
          param, and drop the whole block when none do (a prompt-only pipeline like
          pp-self, whose tunable surface is the prompt fields above). */}
      {lockableNodes.length > 0 ? (
        <div className="steer-fork-locks">
          <p className="steer-fork-sub">
            Lock or unlock what the optimizer may tune on this fork — 🔒 holds a param at
            its value, 🔓 lets the optimizer move it.
          </p>
          {lockableNodes.map((nodeId) => (
            <NodeConfigEditor
              key={nodeId}
              mode="search-space"
              locksOnly
              schema={schema}
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

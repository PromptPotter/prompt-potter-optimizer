"use client";

import { useRef, useState } from "react";
import {
  fetchForkPreview,
  postSteerFork,
  type RunLimitOverrides,
  type OperatorForkOverride,
} from "@/lib/api";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useCommand } from "@/lib/hooks/useCommand";
import type { NodeConfigParam, NodeOutputSchema, NodeSearchNarrowing } from "@/lib/api/types";
import { useRoundSource } from "@/lib/hooks/useRoundSource";
import { steeredBy, useAuth } from "@/lib/auth-context";
import type { CyclePath } from "@/lib/ids";
import type { DashboardSnapshot } from "@/lib/poll";
import {
  candidateSearchPoint,
  liveCandidateSearchPoint,
  forkReconcileDefaults,
  configOverridesFromDefaults,
  permittedModels as permittedModelsOf,
  searchPoint,
} from "@/lib/derivations";
import type { PipelineStatus, SelectedCandidate } from "@/lib/types";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { LimitReconcile } from "./LimitReconcile";

// The operator-steered fork form: seeds the point's evolved prompt + config, then mints an
// `operator_steered` fork. The ADDRESS and SCHEMA are the caller's — a Compare channel is not the viewed cycle.

export function SteerForkPanel({
  candidate,
  path,
  dash,
  parentIsLive,
  schema,
  schemaStatus,
  isSingleNode,
  outputSchema,
  onDone,
  onCancel,
}: {
  candidate: SelectedCandidate;
  path: CyclePath;
  // `null` where this browser holds no stream for the cycle; the seed then comes from the round file.
  dash: DashboardSnapshot | null;
  // The parent CYCLE, distinct from `roundIsLive` below (is `candidate.round` in flight).
  parentIsLive: boolean;
  schema: Record<string, NodeConfigParam[]> | null;
  schemaStatus: PipelineStatus;
  isSingleNode: boolean;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const hop = path.at(0);
  const campaignId = hop?.campaignId ?? "";
  const cycleId = hop?.cycleId ?? "";
  const isLive = parentIsLive;
  const { live: roundIsLive, doc } = useRoundSource(path, candidate.round, dash);
  const { me } = useAuth();
  const seed = roundIsLive
    ? liveCandidateSearchPoint(dash, candidate.label)
    : candidateSearchPoint(doc, candidate.candidate_id);
  const seedPrompt = seed?.origin_prompt_fields ?? {};
  const overlay = seed?.pipeline_overlay ?? {};
  // For the EDITOR only; the verdict and the list it names come off the preview below, which reads
  // the campaign's frozen narrowing rather than these seed-moved rows.
  const permittedModels = permittedModelsOf(schema);
  // ADR-0005 babysit act, gated server-side (404 without); reflected here so the row reads read-only.
  const canBabysit = !!me?.capabilities?.includes("campaign.babysit");
  // What the preview read is keyed on; the ref below is what confirm reads. `null` = untouched.
  const [pickedOverlay, setPickedOverlay] = useState<Record<
    string,
    Record<string, unknown>
  > | null>(null);
  // The same verdict `fork-cycle` dispatch computes, asked before confirm rather than 404ing after.
  const steerOverlay = pickedOverlay ?? overlay;
  const preview = useRead(
    campaignId
      ? {
          key: `${campaignId}\x1f${JSON.stringify(steerOverlay)}`,
          fetch: (signal) => fetchForkPreview(campaignId, steerOverlay, signal),
        }
      : null,
    { surface: "fork-preview" },
  );
  const verdict = readyData(preview);
  const steersDisallowedModel = verdict?.steers_disallowed_model ?? false;
  const permittedList = [
    ...new Set(Object.values(verdict?.permitted_models ?? {}).flat()),
  ];

  // Refs, not state: a blur firing just before the Confirm click is already reflected.
  // `null` = untouched, so confirm uses the loaded seed.
  const editedPrompt = useRef<Record<string, unknown> | null>(null);
  const editedOverlay = useRef<Record<string, Record<string, unknown>> | null>(null);
  // Empty = inherit the campaign's mint-time narrowing unchanged.
  const editedNarrowing = useRef<Record<string, NodeSearchNarrowing>>({});
  // Seeded with the SHOWN "remaining" defaults, so an untouched confirm never inherits the full budget.
  const limits = useRef<RunLimitOverrides>(
    configOverridesFromDefaults(forkReconcileDefaults(dash)),
  );

  const cmd = useCommand<"steer-fork">("steer-fork");
  const pending = cmd.pending !== null;

  const confirm = () => {
    if (!campaignId || !cycleId) return;
    const forkSeed: OperatorForkOverride = {
      origin_prompt_fields: editedPrompt.current ?? seedPrompt,
      pipeline_overlay: editedOverlay.current ?? overlay,
      config_overrides: limits.current,
      ...(Object.keys(editedNarrowing.current).length > 0
        ? { optimizer_narrowing: editedNarrowing.current }
        : {}),
    };
    void cmd.run(
      "steer-fork",
      () =>
        postSteerFork(campaignId, cycleId, candidate.round, candidate.candidate_id, {
          seed: forkSeed,
          steeredBy: steeredBy(me),
          // The live parent's worker exits first, so the fork launch doesn't race its loop.
          pauseFirst: isLive,
        }),
      onDone,
    );
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

      {/* A verdict that never arrived is not a clean steer. */}
      {preview.status === "failed" && canBabysit ? (
        <p className="steer-fork-note" role="alert">
          Couldn&apos;t check this steer against what the campaign permits. Confirming may mark
          the branch operator-babysat (grade C).
        </p>
      ) : null}

      {steersDisallowedModel && canBabysit ? (
        <div className="steer-fork-babysit">
          <p className="steer-fork-babysit-warn" role="note">
            This model isn&apos;t one this node permits
            {permittedList.length > 0 ? ` (${permittedList.join(", ")})` : ""}. Steering to
            it marks this branch — and every round after it — operator-babysat (grade C):
            excluded from clean comparison, origin reuse, and the L4 rollup. The
            measurement is still recorded; it just isn&apos;t a clean one. Pick a permitted
            model to keep a clean branch.
          </p>
        </div>
      ) : null}

      <NodeSurface
        node={null}
        point={searchPoint(seedPrompt, overlay)}
        overlay={overlay}
        schema={schema}
        schemaStatus={schemaStatus}
        isSingleNode={isSingleNode}
        outputSchema={outputSchema}
        mode="values"
        babysitEditable={canBabysit}
        permittedModels={permittedModels}
        onApply={(p) => {
          editedPrompt.current = p.origin_prompt_fields ?? {};
        }}
        onConfigChange={(o) => {
          editedOverlay.current = o;
          setPickedOverlay(o);
        }}
        onNarrowing={(nodeId, n) => {
          editedNarrowing.current = { ...editedNarrowing.current, [nodeId]: n };
        }}
      />

      <LimitReconcile onChange={(l) => (limits.current = l)} />

      {cmd.failure && (
        <span className="steer-fork-err" role="alert">fork: {cmd.failure.message}</span>
      )}

      <div className="steer-fork-actions">
        <button type="button" className="steer-fork-cancel" onClick={onCancel} disabled={pending}>
          Cancel
        </button>
        <button
          type="button"
          className="steer-fork-confirm"
          onClick={confirm}
          disabled={pending}
          title="Mint a fork rooted at this searchpoint, carrying your edits. Tagged operator_steered in lineage."
        >
          {pending ? "Forking…" : isLive ? "Stop & steer-fork" : "Confirm steered fork"}
        </button>
      </div>
    </div>
  );
}

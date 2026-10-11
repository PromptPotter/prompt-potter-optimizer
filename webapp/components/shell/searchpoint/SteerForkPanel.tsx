"use client";

import { useRef, useState } from "react";
import {
  forkPreviewRead,
  postSteerFork,
  type RunLimitOverrides,
  type ForkSeed,
} from "@/lib/api";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useCommand } from "@/lib/hooks/useCommand";
import type { ParamIntent } from "@/lib/api/types";
import { useRound } from "@/lib/hooks/useRound";
import { useAuth } from "@/lib/auth-context";
import { pathRoot, type CyclePath } from "@/lib/ids";
import { useDashboardAt } from "@/lib/poll";
import { useCycleEntry } from "@/lib/registry";
import { phaseWalks } from "@/lib/run-phase";
import {
  candidateSearchPoint,
  liveCandidateSearchPoint,
  permittedModels as permittedModelsOf,
  searchPoint,
} from "@/lib/derivations";
import type { NodeSchemaReading, SelectedCandidate } from "@/lib/types";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { LimitReconcile } from "./LimitReconcile";

export function SteerForkPanel({
  candidate,
  path,
  schema,
  onDone,
  onCancel,
}: {
  candidate: SelectedCandidate;
  path: CyclePath;
  schema: NodeSchemaReading;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { campaignId, cycleId } = pathRoot(path);
  const dash = useDashboardAt(path);
  const isLive = phaseWalks(useCycleEntry(campaignId, cycleId)?.run_phase);
  const { unfiled, doc } = useRound(path, candidate.round);
  const { me } = useAuth();
  const seed = unfiled
    ? liveCandidateSearchPoint(dash, candidate.label)
    : candidateSearchPoint(doc, candidate.candidate_id);
  const seedPrompt = seed?.origin_prompt_fields ?? {};
  const overlay = seed?.pipeline_overlay ?? {};
  // For the EDITOR only: the verdict reads the campaign's frozen narrowing, not these rows.
  const permittedModels = permittedModelsOf(schema.config);
  const canBabysit = !!me?.capabilities?.includes("campaign.babysit");
  const [pickedOverlay, setPickedOverlay] = useState<Record<
    string,
    Record<string, unknown>
  > | null>(null);
  const steerOverlay = pickedOverlay ?? overlay;
  const preview = useRead(forkPreviewRead(campaignId, steerOverlay));
  const verdict = readyData(preview);
  const checking = preview.status === "loading";
  const permittedList = [
    ...new Set(Object.values(verdict?.permitted_models ?? {}).flat()),
  ];

  // Refs, not state: a blur firing just before the Confirm click is already reflected.
  const editedPrompt = useRef<Record<string, unknown> | null>(null);
  const editedOverlay = useRef<Record<string, Record<string, unknown>> | null>(null);
  const editedNarrowing = useRef<Record<string, ParamIntent[]>>({});
  const limits = useRef<RunLimitOverrides>({});

  const cmd = useCommand<"steer-fork">("steer-fork");
  const pending = cmd.pending !== null;

  const confirm = () => {
    const forkSeed: ForkSeed = {
      origin_prompt_fields: editedPrompt.current ?? seedPrompt,
      pipeline_overlay: editedOverlay.current ?? overlay,
      config_overrides: limits.current,
      ...(Object.keys(editedNarrowing.current).length > 0
        ? { node_narrowing: editedNarrowing.current }
        : {}),
    };
    void cmd.run(
      "steer-fork",
      () =>
        postSteerFork(path, candidate.round, candidate.candidate_id, { seed: forkSeed }),
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

      {preview.status === "failed" && canBabysit ? (
        <p className="steer-fork-note" role="alert">
          Couldn&apos;t check this steer against what the campaign permits. Confirming may mark
          the branch operator-babysat (grade C).
        </p>
      ) : null}

      {checking ? (
        <p className="steer-fork-note" role="status">
          Checking this steer against what the campaign permits…
        </p>
      ) : null}

      {verdict?.steers_disallowed_model && canBabysit ? (
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

      <LimitReconcile path={path} onChange={(l) => (limits.current = l)} />

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
          disabled={pending || checking}
          title="Mint a fork rooted at this searchpoint, carrying your edits. Tagged operator_steered in lineage."
        >
          {pending ? "Forking…" : isLive ? "Stop & steer-fork" : "Confirm steered fork"}
        </button>
      </div>
    </div>
  );
}

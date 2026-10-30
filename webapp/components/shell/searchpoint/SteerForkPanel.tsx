"use client";

import { useRef, useState } from "react";
import {
  cycleAddress,
  forkPreviewRead,
  postCommand,
  type RunLimitOverrides,
  type ForkSeed,
} from "@/lib/api";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useCommand } from "@/lib/hooks/useCommand";
import type { NodePrompt, ParamIntent } from "@/lib/api/types";
import { useRound } from "@/lib/hooks/useRound";
import { useAuth } from "@/lib/state/auth";
import { encodeCyclePath, pathRoot, type CyclePath } from "@/lib/ids";
import { useDashboardAt } from "@/lib/poll";
import { useEntry } from "@/lib/state/registry";
import {
  candidateSearchPoint,
  phaseWalks,
  liveCandidateSearchPoint,
  permittedModels as permittedModelsOf,
  searchPoint,
} from "@/lib/derivations";
import type { NodeSchemaReading, SelectedCandidate } from "@/lib/types";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { FailureLine } from "@/components/ui";
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
  const dash = useDashboardAt(path);
  const root = pathRoot(path);
  const rootCourse = useEntry(encodeCyclePath([root]));
  const isLive = phaseWalks(rootCourse?.level === "course" ? rootCourse.state.run_phase : undefined);
  const { unfiled, doc } = useRound(path, candidate.round);
  const { me } = useAuth();
  const seed = unfiled
    ? liveCandidateSearchPoint(dash, candidate.arm)
    : candidateSearchPoint(doc, candidate.arm);
  const seedPrompts = seed?.origin_prompts ?? {};
  const overlay = seed?.pipeline_overlay ?? {};
  // For the EDITOR only: the verdict reads the campaign's frozen narrowing, not these rows.
  const permittedModels = permittedModelsOf(schema.config);
  const canBabysit = !!me?.capabilities.includes("campaign.babysit");
  const [pickedOverlay, setPickedOverlay] = useState<Record<
    string,
    Record<string, unknown>
  > | null>(null);
  const steerOverlay = pickedOverlay ?? overlay;
  const preview = useRead(forkPreviewRead(root.campaignId, steerOverlay));
  const verdict = readyData(preview);
  const checking = preview.status === "loading";
  const permittedList = [
    ...new Set(Object.values(verdict?.permitted_models ?? {}).flat()),
  ];

  // Refs, not state: a blur firing just before the Confirm click is already reflected.
  const editedPrompts = useRef<Record<string, NodePrompt> | null>(null);
  const editedOverlay = useRef<Record<string, Record<string, unknown>> | null>(null);
  const editedNarrowing = useRef<Record<string, ParamIntent[]>>({});
  const limits = useRef<RunLimitOverrides>({});

  const cmd = useCommand<"steer-fork">("steer-fork");
  const pending = cmd.pending !== null;

  const confirm = () => {
    const forkSeed: ForkSeed = {
      origin_prompts: editedPrompts.current ?? seedPrompts,
      pipeline_overlay: editedOverlay.current ?? overlay,
      config_overrides: limits.current,
      ...(Object.keys(editedNarrowing.current).length > 0
        ? { node_narrowing: editedNarrowing.current }
        : {}),
    };
    void cmd.run(
      "steer-fork",
      () =>
        postCommand("fork-cycle", {
          ...cycleAddress(path),
          round: candidate.round,
          candidate_id: candidate.arm,
          seed: forkSeed,
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
          This searchpoint isn&apos;t loaded yet — its prompt appears here once it is, and
          seeds the fork&apos;s origin.
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
        point={searchPoint(seedPrompts, overlay)}
        overlay={overlay}
        schema={schema}
        mode="values"
        babysitEditable={canBabysit}
        permittedModels={permittedModels}
        onApply={(p) => {
          // The editor writes fields; each node's shots ride the seed unchanged.
          const fields = p.origin_prompts ?? {};
          editedPrompts.current = Object.fromEntries(
            Object.entries(fields).flatMap(([node, f]) => {
              const seeded = seedPrompts[node];
              return seeded ? [[node, { fields: f, shots: seeded.shots }]] : [];
            }),
          );
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

      <FailureLine failure={cmd.failure} lead="fork:" />

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

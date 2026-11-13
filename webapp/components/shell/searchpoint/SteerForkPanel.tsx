import { useRef, useState } from "react";
import {
  commands,
  cycleAddress,
  forkPreviewRead,
  type RunLimitOverrides,
  type ForkSeed,
} from "@/lib/api";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useCommand } from "@/lib/hooks/useCommand";
import { type NodePrompt, type ParamIntent, STEER_OUTSIDE_INFO } from "@/lib/api/types";
import { useRound } from "@/lib/hooks/useRound";
import { encodeCyclePath, pathRoot, type CyclePath } from "@/lib/ids";
import { useEntry } from "@/lib/hooks/useRegistry";
import { phaseWalks, searchPoint } from "@/lib/derivations";
import type { NodeSchemaReading, SelectedCandidate } from "@/lib/types";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";
import { Empty, FailureLine, HoverCard, ReadState, Said } from "@/components/ui";
import { LimitReconcile } from "./LimitReconcile";

// The editor hands back the candidate's whole config with the edit laid in; the steer is the edit.
function editOf(
  next: Record<string, Record<string, unknown>>,
  seeded: Record<string, unknown>,
): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const [node, cfg] of Object.entries(next)) {
    const was = (seeded[node] ?? {}) as Record<string, unknown>;
    const moved = Object.entries(cfg).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(was[k]));
    if (moved.length > 0) out[node] = Object.fromEntries(moved);
  }
  return out;
}

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
  const root = pathRoot(path);
  const rootCourse = useEntry(encodeCyclePath([root]));
  const isLive = phaseWalks(rootCourse?.level === "course" ? rootCourse.state.status.run_phase : undefined);
  const round = useRound(path, candidate.round);
  const seed = round.seed(candidate.arm);
  const seedPrompts = seed?.origin_prompts ?? {};
  const overlay = seed?.pipeline_overlay ?? {};
  const [pickedOverlay, setPickedOverlay] = useState<Record<
    string,
    Record<string, unknown>
  > | null>(null);
  // The fork runs the candidate's own record; only the operator's edit is a steer.
  const preview = useRead(forkPreviewRead(root.campaignId, pickedOverlay ?? {}));
  const checking = preview.status === "loading";
  const refused = readyData(preview)?.steer === "refused";

  // Refs, not state: a blur firing just before the Confirm click is already reflected.
  const editedPrompts = useRef<Record<string, NodePrompt> | null>(null);
  const editedOverlay = useRef<Record<string, Record<string, unknown>> | null>(null);
  const editedNarrowing = useRef<Record<string, ParamIntent[]>>({});
  const limits = useRef<RunLimitOverrides>({});

  const cmd = useCommand<"steer-fork">("steer-fork");
  const pending = cmd.pending !== null;

  const confirm = () => {
    const { budget, nodes } = limits.current;
    const forkSeed: ForkSeed = {
      origin_prompts: editedPrompts.current ?? seedPrompts,
      pipeline_overlay: editedOverlay.current ?? {},
      config_overrides: nodes ? { nodes } : {},
      ...(Object.keys(editedNarrowing.current).length > 0
        ? { node_narrowing: editedNarrowing.current }
        : {}),
    };
    void cmd.run(
      "steer-fork",
      () =>
        commands["fork-cycle"]({
          ...cycleAddress(path),
          round: candidate.round,
          candidate_id: candidate.arm,
          seed: forkSeed,
          ...(budget ? { budget } : {}),
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

      {!seed &&
        (round.read.status === "failed" ? (
          <FailureLine failure={round.read.failure} lead="Could not read this searchpoint." />
        ) : (
          <Empty>
            This searchpoint isn&apos;t loaded yet — its prompt appears here once it is, and
            seeds the fork&apos;s origin.
          </Empty>
        ))}

      <ReadState read={preview} subject="what the campaign permits for this steer">
        {(verdict) => {
          if (verdict.steer === "clean") return null;
          const { title, detail, tone } = STEER_OUTSIDE_INFO[verdict.steer];
          // The set the verdict compared against, never the editor's rows.
          const permitted = Object.entries(verdict.permitted)
            .map(([node, models]) => `${node}: ${models.join(", ")}`)
            .join(" · ");
          return (
            <Said
              tone={tone}
              label={title}
              detail={`${detail} Permitted — ${permitted || "no model on any node"}.`}
            />
          );
        }}
      </ReadState>

      <NodeSurface
        node={null}
        point={searchPoint(seedPrompts, overlay)}
        overlay={overlay}
        schema={schema}
        mode="values"
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
          const edit = editOf(o, overlay);
          editedOverlay.current = edit;
          setPickedOverlay(edit);
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
        <HoverCard content="Mint a fork rooted at this searchpoint, carrying your edits. Tagged operator_steered in lineage.">
          <button
            type="button"
            className="steer-fork-confirm"
            onClick={confirm}
            disabled={pending || checking || refused}
          >
            {pending ? "Forking…" : isLive ? "Stop & steer-fork" : "Confirm steered fork"}
          </button>
        </HoverCard>
      </div>
    </div>
  );
}

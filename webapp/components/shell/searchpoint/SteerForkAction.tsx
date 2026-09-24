"use client";
// The affordance for steering a searchpoint — button, modal, and the refusal below the top level.

import { useState } from "react";
import type { NodeConfigParam, NodeOutputSchema } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import type { PipelineStatus, SelectedCandidate } from "@/lib/types";
import type { CyclePath } from "@/lib/ids";
import { shortId } from "@/lib/format";
import { Dialog } from "@/components/ui";
import { SteerForkPanel } from "./SteerForkPanel";

export function SteerForkAction({
  candidate,
  path,
  dash,
  parentIsLive,
  schema,
  schemaStatus,
  isSingleNode,
  outputSchema,
}: {
  candidate: SelectedCandidate;
  path: CyclePath | null;
  // `null` where this browser holds no stream for the cycle; the form then seeds from the round file.
  dash: DashboardSnapshot | null;
  parentIsLive: boolean;
  schema: Record<string, NodeConfigParam[]> | null;
  schemaStatus: PipelineStatus;
  isSingleNode: boolean;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
}) {
  const [open, setOpen] = useState(false);

  // A wire fact, not a policy: `ForkCyclePayload` carries no `descend`, so an L4 inner point would
  // match a coincidental id in the outer cycle. Widen the command in `api-openapi.yaml` first.
  if (path && path.length > 1) {
    return (
      <p className="l4-note">
        This searchpoint lives inside {shortId(path[0]?.campaignId ?? "")}&rsquo;s sandbox.{" "}
        <code>fork-cycle</code> is addressed at the top level, so it cannot be cut from here — open
        that run and fork from its own dashboard.
      </p>
    );
  }
  if (!path || path.length === 0) return null;

  return (
    <>
      <button
        type="button"
        className="fork-button"
        onClick={() => setOpen(true)}
        title="Open this searchpoint in the control panel — review or edit its evolved prompt, node config, and run limits, then fork-continue optimizing from it. Edits are optional."
      >
        Steer &amp; fork
      </button>
      {open && (
        <Dialog
          open
          title={`Steer & fork · ${candidate.label}`}
          onClose={() => setOpen(false)}
        >
          <SteerForkPanel
            candidate={candidate}
            path={path}
            dash={dash}
            schema={schema}
            schemaStatus={schemaStatus}
            isSingleNode={isSingleNode}
            outputSchema={outputSchema}
            parentIsLive={parentIsLive}
            onDone={() => setOpen(false)}
            onCancel={() => setOpen(false)}
          />
        </Dialog>
      )}
    </>
  );
}

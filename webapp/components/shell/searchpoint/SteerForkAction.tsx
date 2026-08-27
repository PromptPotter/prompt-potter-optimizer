"use client";
// The AFFORDANCE for steering a searchpoint — the button, the modal it opens, and the one case
// where there is nothing to offer. `SteerForkPanel` is the form; this is how a surface reaches it.
//
// It exists because both hosts of the drill-in were building the same three things by hand, down
// to a byte-identical `title` string — and only one of them carried the refusal below, which made
// the same click safe on Records and wrong on the dashboard.

import { useState } from "react";
import type { NodeConfigParam, NodeOutputSchema } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import type { SelectedCandidate } from "@/lib/types";
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
  outputSchema,
}: {
  candidate: SelectedCandidate;
  // The searchpoint's own address. ONE address, not two: the fork verb and the round file it
  // seeds from both name this cycle, because the refusal below is what removes the case where
  // they could differ. `null` where the host has no address at all — there is then no point to
  // steer, and offering the button would open a form that could only fail on confirm.
  path: CyclePath | null;
  // The live snapshot for that cycle, or `null` where this browser holds no stream for it —
  // exactly one cycle streams, so a surface reading another branch seeds from the round file.
  dash: DashboardSnapshot | null;
  parentIsLive: boolean;
  schema: Record<string, NodeConfigParam[]> | null;
  outputSchema: Record<string, NodeOutputSchema | null> | null;
}) {
  const [open, setOpen] = useState(false);

  // **It refuses below the top level, and that is a WIRE fact rather than a policy.**
  // `ForkCyclePayload` extends `CyclePayload`, not `DescendableCyclePayload` — it carries no
  // `descend`, so the only `(round, candidate_id)` it can name is one of the addressed cycle's
  // own. Asked to fork an L4 inner searchpoint it would either resolve nothing or match a
  // coincidental id in the outer cycle and cut the wrong point, which is worse than refusing.
  // Making it reachable means widening the command contract, declared in `m12-api-openapi.yaml`
  // first.
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
      {/* Steering is its own act with its own home — a modal that opens straight from the
          drill-in (no tab hop). The fork continues from this searchpoint (always
          `operator_steered`); edits are optional. */}
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

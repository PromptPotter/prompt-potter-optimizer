"use client";

import { useState } from "react";
import type { NodeSchemaReading, SelectedCandidate } from "@/lib/types";
import type { CyclePath } from "@/lib/ids";
import { Dialog } from "@/components/ui";
import { SteerForkPanel } from "./SteerForkPanel";

export function SteerForkAction({
  candidate,
  path,
  schema,
}: {
  candidate: SelectedCandidate;
  path: CyclePath | null;
  schema: NodeSchemaReading;
}) {
  const [open, setOpen] = useState(false);

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
            schema={schema}
            onDone={() => setOpen(false)}
            onCancel={() => setOpen(false)}
          />
        </Dialog>
      )}
    </>
  );
}

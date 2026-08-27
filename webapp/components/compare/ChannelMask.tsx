"use client";
// Compare's binding of the shared scoring-mask editor (`components/shell/mask/`) to ONE channel's
// address. The form is the dashboard's; what is Compare-specific is the ownership — a mask lives
// on the channel's address, so a board can carry the record and two counterfactuals of it at once,
// and applying an edit REPLACES that channel in place rather than moving a global state.
//
// The grammar itself is spelled by `lib/api/reads.ts::maskedSubject` and parsed by the server.
// Nothing here splits an address.

import { useState } from "react";
import { EVALUATOR_META } from "@/lib/api/types.generated";
import { maskedSubject } from "@/lib/api/reads";
import type { SubjectReading } from "@/lib/api/types";
import { ScoringMaskEditor } from "@/components/shell/mask/ScoringMaskEditor";
import {
  buildRows,
  emptyMask,
  lensOf,
  type ScoringMask,
} from "@/components/shell/mask/scoring-mask";

// Every registered evaluator, all applicable. Compare has no cycle to narrow against — a board
// can span campaigns whose pipelines carry different nodes — so it offers the whole registry and
// lets the server report a term a given channel's rows cannot answer.
const ALL_ROWS = buildRows(
  EVALUATOR_META,
  new Set(EVALUATOR_META.map((m) => m.name)),
);
// No campaign-wide realized formula on a board that can span several, so no tile is marked as
// "in the actual formula" rather than one campaign's being shown as if it were everyone's.
const NONE: ReadonlySet<string> = new Set();

export function ChannelMask({
  subject,
  invalid,
  onApply,
  onClose,
}: {
  // The channel being masked, as served — its `mask` seeds the fields, so re-opening the editor
  // shows what is actually on screen rather than an empty form.
  subject: SubjectReading;
  invalid: string | null;
  // Replaces this channel's address with the masked one. A no-op edit is not applied: the key
  // would be identical and the refetch pointless.
  onApply: (from: string, to: string) => void;
  onClose: () => void;
}) {
  // A SERVED lens is a string — the wire has already collapsed whatever built it — and
  // decomposing one back into weights here would be the formula parse this layer does not do.
  // So a channel that already carries one opens in Expression mode holding it verbatim; switching
  // to Weights is an explicit "write a new one".
  const served = subject.mask?.lens ?? "";
  const [mask, setMask] = useState<ScoringMask>(() =>
    served ? { kind: "expression", lens: served } : emptyMask(),
  );
  const [samples, setSamples] = useState((subject.mask?.samples ?? []).join(","));

  const commit = (next: ScoringMask, nextSamples: string) => {
    const address = maskedSubject(subject, {
      lens: lensOf(next),
      samples: nextSamples.trim(),
    });
    if (address !== subject.key) onApply(subject.key, address);
  };

  return (
    <div className="cmp-expr" role="group" aria-label={`Scoring mask for ${subject.label}`}>
      <p className="cmp-expr-label">
        What if <code>{subject.label}</code> had been scored differently?
      </p>
      <ScoringMaskEditor
        rows={ALL_ROWS}
        inActive={NONE}
        mask={mask}
        onMask={(next) => {
          setMask(next);
          commit(next, samples);
        }}
        seeded="none"
        samples={samples}
        onSamples={(raw) => {
          setSamples(raw);
          commit(mask, raw);
        }}
        invalid={invalid}
      />
      <p className="l4-subtle">
        A criterion re-decides this branch&rsquo;s elections and reads it at the winner that would
        have stood; a sample list drops rows, so every value left is one this branch actually
        recorded. Clear both to go back to the record.
      </p>
      <button type="button" className="cmp-link" onClick={onClose}>
        Done
      </button>
    </div>
  );
}

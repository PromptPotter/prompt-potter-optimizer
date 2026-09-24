"use client";
// Compare's binding of the shared scoring-mask editor (`components/shell/mask/`) to one channel's address.
// Applying REPLACES that channel in place; the grammar is `lib/api/reads.ts::maskedSubject`'s — never split here.

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

// The whole registry: a board can span pipelines, and the server reports a term a channel cannot answer.
const ALL_ROWS = buildRows(
  EVALUATOR_META,
  new Set(EVALUATOR_META.map((m) => m.name)),
);
// No campaign-wide formula on a multi-campaign board, so no tile is marked "in the actual formula".
const NONE: ReadonlySet<string> = new Set();

export function ChannelMask({
  subject,
  invalid,
  onApply,
  onClose,
}: {
  subject: SubjectReading;
  invalid: string | null;
  // A no-op edit is not applied (identical key, pointless refetch).
  onApply: (from: string, to: string) => void;
  onClose: () => void;
}) {
  // A SERVED lens is a string; decomposing it back into weights is the formula parse this layer does
  // not do. So it opens in Expression mode, verbatim.
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

import { useState } from "react";
import { maskedSubject } from "@/lib/api/reads";
import type { SubjectReading } from "@/lib/api/types";
import { Criterion } from "@/components/criterion/Criterion";
import type { ReadFailure } from "@/lib/read-cache";
import { useCellTerms } from "@/lib/hooks/useConnector";
import { NO_DIALS, lensOf, type ScoringMask } from "@/lib/state/scoring-mask";

export function ChannelMask({
  subject,
  invalid,
  onApply,
  onClose,
}: {
  subject: SubjectReading;
  invalid: ReadFailure | null;
  onApply: (from: string, to: string) => void;
  onClose: () => void;
}) {
  const served = subject.mask?.lens ?? "";
  const [mask, setMask] = useState<ScoringMask>(() =>
    served ? { kind: "expression", lens: served } : NO_DIALS,
  );
  const [samples, setSamples] = useState((subject.mask?.samples ?? []).join(","));

  const cellTerms = useCellTerms();
  const commit = (next: ScoringMask, nextSamples: string) => {
    const address = maskedSubject(subject, {
      lens: lensOf(next, cellTerms),
      samples: nextSamples.trim(),
    });
    if (address !== subject.key) onApply(subject.key, address);
  };

  return (
    <div className="cmp-expr" role="group" aria-label={`Scoring mask for ${subject.label}`}>
      <p className="cmp-expr-label">
        What if <code>{subject.label}</code> had been scored differently?
      </p>
      <Criterion
        startRung={1}
        mask={mask}
        onMask={(next) => {
          setMask(next);
          commit(next, samples);
        }}
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

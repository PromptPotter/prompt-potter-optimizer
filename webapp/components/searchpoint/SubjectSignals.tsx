// served: `SubjectReading.cell_means` — each side a mean over that point's own cells, worded.
import type { OriginEvidence } from "@/lib/hooks/useOriginEvidence";
import { cx } from "@/lib/cx";
import { Button } from "@/components/ui";

export function SubjectSignals({
  origin,
  shown,
  compare,
}: Pick<OriginEvidence, "origin" | "shown" | "compare">) {
  const rows = (origin?.cell_means ?? []).map((was) => ({
    was,
    now: shown?.cell_means.find((m) => m.key === was.key),
  }));
  if (rows.length === 0) return null;

  return (
    <div className="subject-signals">
      <dl>
        {rows.map(({ was, now }) => (
          <div key={was.key} className={cx("subject-signal", was.lead && "is-lead")}>
            <dt>{was.label}</dt>
            <dd>
              {now === undefined ? (
                was.text
              ) : (
                <>
                  <span className="subject-signal-was">{was.text}</span>
                  <span className="subject-signal-arrow" aria-label="origin to this searchpoint">
                    →
                  </span>
                  <span
                    className={cx(
                      "subject-signal-now",
                      now.value < was.value && "is-lower",
                      now.value > was.value && "is-higher",
                    )}
                  >
                    {now.text}
                  </span>
                </>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {compare && (
        <Button variant="ghost" onClick={compare}>
          Open in Compare
        </Button>
      )}
    </div>
  );
}

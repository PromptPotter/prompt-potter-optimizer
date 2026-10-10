"use client";
// served: `SubjectReading.cell_means` — each side a mean over that point's own cells.
import type { OriginEvidence } from "@/lib/hooks/useOriginEvidence";
import { CELL_MEAN_ROWS } from "@/lib/cell-means";
import { cx } from "@/lib/cx";
import { Button } from "@/components/ui";

export function SubjectSignals({
  origin,
  shown,
  compare,
}: Pick<OriginEvidence, "origin" | "shown" | "compare">) {
  const rows = CELL_MEAN_ROWS.flatMap((r) => {
    const was = origin?.cell_means?.[r.key];
    if (was === undefined) return [];
    return [{ ...r, was, now: shown?.cell_means?.[r.key] }];
  });
  if (rows.length === 0) return null;

  return (
    <div className="subject-signals">
      <dl>
        {rows.map((r) => (
          <div key={r.key} className={cx("subject-signal", r.lead && "is-lead")}>
            <dt>{r.label}</dt>
            <dd>
              {r.now === undefined ? (
                r.fmt(r.was)
              ) : (
                <>
                  <span className="subject-signal-was">{r.fmt(r.was)}</span>
                  <span className="subject-signal-arrow" aria-label="origin to this searchpoint">
                    →
                  </span>
                  <span
                    className={cx(
                      "subject-signal-now",
                      r.now < r.was && "is-lower",
                      r.now > r.was && "is-higher",
                    )}
                  >
                    {r.fmt(r.now)}
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

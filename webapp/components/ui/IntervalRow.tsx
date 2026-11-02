import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./IntervalRow.module.css";

export type IntervalDomain = readonly [number, number];

/** ONE padded axis over every value a set of rows draws: a per-row scale hides how they differ. */
export function intervalDomain(values: readonly number[]): IntervalDomain | null {
  if (values.length === 0) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = (hi - lo || 1) * 0.1;
  return [lo - pad, hi + pad];
}

const at = ([d0, d1]: IntervalDomain, v: number) => `${((v - d0) / (d1 - d0)) * 100}%`;

/** A label, a point and its interval on a shared axis, and the value in words. The axis is drawn in
 *  percent of whatever width the row leaves it; the marks take the row's `currentColor` (the caller's
 *  `className`) unless a categorical ink is passed. */
export function IntervalRow({
  label,
  title,
  value,
  domain,
  point,
  lo,
  hi,
  reference,
  ariaLabel,
  className,
  pointInk,
  intervalInk,
}: {
  label: ReactNode;
  title?: string;
  value: ReactNode;
  domain: IntervalDomain;
  point: number | null;
  lo: number | null;
  hi: number | null;
  /** The value a reading is judged against, ruled across the axis (a lift's zero). */
  reference?: number;
  ariaLabel: string;
  className?: string;
  pointInk?: string;
  intervalInk?: string;
}) {
  return (
    <div className={cx(s.row, className)}>
      <span className={s.label} title={title}>
        {label}
      </span>
      <svg className={s.axis} role="img" aria-label={ariaLabel}>
        {reference !== undefined && (
          <line
            className={s.reference}
            x1={at(domain, reference)}
            x2={at(domain, reference)}
            y1="10%"
            y2="90%"
          />
        )}
        {lo !== null && hi !== null && (
          <line
            x1={at(domain, lo)}
            x2={at(domain, hi)}
            y1="50%"
            y2="50%"
            stroke={intervalInk ?? "currentColor"}
            strokeWidth={1.5}
          />
        )}
        {point !== null && (
          <circle cx={at(domain, point)} cy="50%" r={3.5} fill={pointInk ?? "currentColor"} />
        )}
      </svg>
      <span className={s.value}>{value}</span>
    </div>
  );
}

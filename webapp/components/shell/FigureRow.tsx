"use client";

import type { ReactNode } from "react";
import { Term } from "@/components/ui";
import type { Said } from "@/lib/api/types.generated";
import { cx } from "@/lib/cx";

// The one row of figures every entry and the masthead draw: label over value, a sub line under. It wraps.
export function FigureRow({ children }: { children: ReactNode }) {
  return <dl className="figure-row">{children}</dl>;
}

// A served figure row; the cell is never a control: `detail` joins its value's Term, `action` a control on its own line.
// A row louder than quiet leads its value with its tone's dot.
export function Figure({
  row,
  detail,
  action,
}: {
  row: Said;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  const { label, value, sub, tone } = row;
  const marked = tone !== "quiet";
  const hover = row.detail ? <>{row.detail}{detail && <>. {detail}</>}</> : detail;
  return (
    <div className={cx("figure", marked && `tone-${tone}`)}>
      <dt>{label}</dt>
      <dd className="figure-value">
        {marked && <span className="phase-dot" aria-hidden="true" />}
        {hover ? <Term content={hover}>{value}</Term> : value}
      </dd>
      {/* Clamped: a served reason can be a provider's whole error; `title` repeats it whole. */}
      {sub && (
        <dd className="figure-sub" title={sub}>
          {sub}
        </dd>
      )}
      {action && <dd className="figure-action">{action}</dd>}
    </div>
  );
}

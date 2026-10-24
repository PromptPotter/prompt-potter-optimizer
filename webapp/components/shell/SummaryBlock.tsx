"use client";

import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import type { SummaryFacts } from "@/lib/derivations";

export function SummaryBlock({
  facts,
  dense = false,
  lede,
  actions,
}: {
  facts: SummaryFacts;
  dense?: boolean;
  lede?: ReactNode;
  actions?: ReactNode;
}) {
  const state = facts.state?.status.label;
  return (
    <div className={cx("summary-block", dense && "is-dense")}>
      <header className="summary-block-head">
        <div className="summary-block-titleline">
          <span className="summary-block-title">{facts.label}</span>
          {facts.tags?.map((t, i) => (
            <span key={i} className="summary-block-tag">
              {t.label}
            </span>
          ))}
          {state && <span className="summary-block-state">{state}</span>}
        </div>
        {actions}
      </header>
      {lede}
      {facts.stats.length > 0 && (
        <dl className="summary-block-stats">
          {facts.stats.map((s) => (
            <div key={s.label} className="summary-block-stat">
              <dt>{s.label}</dt>
              <dd className={cx("summary-block-stat-value", s.className)}>{s.value}</dd>
              {/* Clamped: a served reason can be a provider's whole error; `title` repeats it whole. */}
              {s.sub && (
                <dd className="summary-block-stat-sub" title={s.sub}>
                  {s.sub}
                </dd>
              )}
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

"use client";

import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import type { HierarchyEntry } from "@/lib/api";

export function SummaryBlock({
  facts,
  dense = false,
  lede,
  actions,
}: {
  facts: HierarchyEntry;
  dense?: boolean;
  lede?: ReactNode;
  actions?: ReactNode;
}) {
  const state = "state" in facts ? facts.state.status.label : null;
  return (
    <div className={cx("summary-block", dense && "is-dense")}>
      <header className="summary-block-head">
        <div className="summary-block-titleline">
          <span className="summary-block-title">{facts.label}</span>
          {facts.tags.map((t) => (
            <span key={`${t.kind}:${t.value}`} className="summary-block-tag">
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
            <div key={s.key} className="summary-block-stat">
              <dt>{s.label}</dt>
              <dd className="summary-block-stat-value">{s.value}</dd>
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

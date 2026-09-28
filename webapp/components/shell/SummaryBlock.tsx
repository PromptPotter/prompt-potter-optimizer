"use client";
// A campaign's standing in one block for every host that summarises one — the sidebar hover card, a
// Compare column, the chat's finished-run item. `dense` is the host's density, never other facts.

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
  return (
    <div className={cx("summary-block", dense && "is-dense")}>
      <header className="summary-block-head">
        <div className="summary-block-titleline">
          <span className="summary-block-title">{facts.title}</span>
          {facts.tags?.map((t) => (
            <span key={t} className="summary-block-tag">
              {t}
            </span>
          ))}
          {facts.state && <span className="summary-block-state">{facts.state}</span>}
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

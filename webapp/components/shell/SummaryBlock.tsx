import type { ReactNode } from "react";
import { Said } from "@/components/ui";
import { cx } from "@/lib/cx";
import type { HierarchyEntry, UnstartedCampaign } from "@/lib/api";
import { Figure, FigureRow } from "./FigureRow";

// An entry's served lines under its title, then its figures.
export function SummaryBlock({
  entry,
  dense = false,
  actions,
}: {
  entry: HierarchyEntry | UnstartedCampaign;
  dense?: boolean;
  actions?: ReactNode;
}) {
  return (
    <div className={cx("summary-block", dense && "is-dense")}>
      <header className="summary-block-head">
        <div className="summary-block-titleline">
          <span className="summary-block-title">{entry.label}</span>
          {entry.tags.map((t) => (
            <span key={`${t.kind}:${t.value}`} className="summary-block-tag">
              {t.label}
            </span>
          ))}
        </div>
        {actions}
      </header>
      {entry.lines.map((row) => (
        <Said key={row.key} tone={row.tone} label={row.label} sub={row.sub} detail={row.detail} />
      ))}
      {entry.figures.length > 0 && (
        <FigureRow>
          {entry.figures.map((row) => (
            <Figure key={row.key} row={row} />
          ))}
        </FigureRow>
      )}
    </div>
  );
}

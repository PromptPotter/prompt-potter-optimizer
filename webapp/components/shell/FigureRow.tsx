"use client";

import type { ReactNode } from "react";
import { Term } from "@/components/ui";
import type { FamilyId } from "@/lib/address";
import type { Said } from "@/lib/api/types.generated";
import { cx } from "@/lib/cx";
import { useWorkspace } from "@/lib/workspace";
import { FAMILIES } from "./families";

// The one row of figures every entry and the masthead draw: label over value, a sub line under. It wraps.
export function FigureRow({ children }: { children: ReactNode }) {
  return <dl className="figure-row">{children}</dl>;
}

// A served figure row: `detail` joins its value's Term. A row louder than quiet leads its value with
// its tone's dot. With `opens`, its label is that view's door, lit while the view is open.
export function Figure({
  row,
  detail,
  opens = null,
}: {
  row: Said;
  detail?: ReactNode;
  opens?: FamilyId | null;
}) {
  const { label, value, sub, tone } = row;
  const marked = tone !== "quiet";
  const hover = row.detail ? <>{row.detail}{detail && <>. {detail}</>}</> : detail;
  return (
    <div className={cx("figure", marked && `tone-${tone}`)}>
      <dt>{opens ? <FigureDoor label={label} family={opens} /> : label}</dt>
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
    </div>
  );
}

// An open view no served figure names: its door, with no value under it.
export function ViewDoor({ family }: { family: FamilyId }) {
  return (
    <div className="figure">
      <dt>
        <FigureDoor label={FAMILIES[family].label} family={family} />
      </dt>
      <dd className="figure-value" />
    </div>
  );
}

function FigureDoor({ label, family }: { label: string; family: FamilyId }) {
  const { views, open } = useWorkspace();
  const lit = views.some((v) => v.family === family);
  const view = FAMILIES[family].label;
  return (
    <button
      type="button"
      className={cx("figure-door", lit && "is-lit")}
      aria-label={view.toLowerCase() === label.toLowerCase() ? `Open ${label}` : `${label}: open ${view}`}
      aria-current={lit || undefined}
      onClick={() => open(family)}
    >
      {label}
    </button>
  );
}

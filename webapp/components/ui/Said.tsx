// The one rendering of a said row (`domain/said.py::Said`): its tone glyphs and inks it in CSS, its
// detail sits behind `Term`. Only a `live` row, whose content changes in place, is announced.

import type { ReactNode } from "react";
import type { Tone } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { Term } from "./Term";

export function Said({
  tone,
  label,
  value = null,
  sub = null,
  detail = null,
  live = false,
  className,
}: {
  tone: Tone;
  label: ReactNode;
  value?: string | null;
  sub?: string | null;
  detail?: string | null;
  live?: boolean;
  className?: string | undefined;
}) {
  return (
    <span className={cx("said", `tone-${tone}`, className)} role={live ? "status" : undefined}>
      {detail ? <Term content={detail}>{label}</Term> : label}
      {value ? <strong className="said-value"> {value}</strong> : null}
      {sub ? <span className="said-sub"> {sub}</span> : null}
    </span>
  );
}

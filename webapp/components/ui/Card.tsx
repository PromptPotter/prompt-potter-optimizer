// With `headingTag="h2"` the title alone is the heading, keeping `actions` out of its accessible name.

import type { CSSProperties, ReactNode } from "react";
import { cx } from "@/lib/cx";

// The one box. It states no size: inside a `.box-grid` it takes one column, or the whole row
// with `span="full"` — the grid decides how wide a column is (panels.css).
export function CardFrame({
  title,
  actions,
  className,
  style,
  headingTag = "div",
  span = "column",
  children,
}: {
  title: ReactNode;
  actions?: ReactNode;
  className?: string;
  style?: CSSProperties;
  headingTag?: "div" | "h2";
  span?: "column" | "full";
  children: ReactNode;
}) {
  return (
    <div className={cx("card", span === "full" && "card-full", className)} style={style}>
      <div className="card-title">
        {headingTag === "h2" ? <h2>{title}</h2> : title}
        {actions}
      </div>
      {children}
    </div>
  );
}

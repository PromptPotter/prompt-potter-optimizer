// With `headingTag="h2"` the title alone is the heading, keeping `actions` out of its accessible name.

import type { CSSProperties, ReactNode } from "react";
import { cx } from "@/lib/cx";

export function CardFrame({
  title,
  actions,
  className,
  style,
  headingTag = "div",
  children,
}: {
  title: ReactNode;
  actions?: ReactNode;
  className?: string;
  style?: CSSProperties;
  headingTag?: "div" | "h2";
  children: ReactNode;
}) {
  return (
    <div className={cx("card", className)} style={style}>
      <div className="card-title">
        {headingTag === "h2" ? <h2>{title}</h2> : title}
        {actions}
      </div>
      {children}
    </div>
  );
}

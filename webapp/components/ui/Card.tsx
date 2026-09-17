// The dashboard's card shell — a `.card` box with a `.card-title` header.
// `title` is the heading content, `actions` the right-aligned badge or
// button group; both sit inside `.card-title` (its flex layout pushes the
// last child right). With `headingTag="h2"` the title alone is the section
// heading, so the actions stay out of its content and its accessible name.

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

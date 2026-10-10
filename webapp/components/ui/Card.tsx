import type { CSSProperties, ReactNode } from "react";
import { cx } from "@/lib/cx";

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
  // "h2" wraps the title alone, keeping `actions` out of the heading's accessible name.
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

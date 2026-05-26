// The dashboard's card shell — a `.card` box with a `.card-title` header.
// `title` is the heading content, `actions` the right-aligned badge or
// button group; both sit inside `.card-title` (its flex layout pushes the
// last child right). `headingTag` is `"h2"` where the title is a section
// heading, `"div"` (default) where it is just a label.

import type { CSSProperties, ReactNode } from "react";

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
  const Header = headingTag;
  return (
    <div className={className ? `card ${className}` : "card"} style={style}>
      <Header className="card-title">
        {title}
        {actions}
      </Header>
      {children}
    </div>
  );
}

import type { ReactNode } from "react";
import { HoverCard } from "./HoverCard";
import s from "./Term.module.css";

export function Term({
  children,
  content,
  className,
  layout = "inline",
}: {
  children: ReactNode;
  content: ReactNode;
  className?: string | undefined;
  layout?: "inline" | "control";
}) {
  return (
    <HoverCard content={content} layout={layout} focusable>
      <span className={className ? `${s.hint} ${className}` : s.hint}>
        {children}
      </span>
    </HoverCard>
  );
}

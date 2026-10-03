"use client";
import type { ReactNode } from "react";
import { HoverCard } from "./HoverCard";
import s from "./Term.module.css";

// Teaching prose on a term — the ONE mechanism, never `title=` (`webapp/CLAUDE.md` § Component
// conventions).
export function Term({
  children,
  content,
  className,
}: {
  children: ReactNode;
  content: ReactNode;
  className?: string;
}) {
  return (
    <HoverCard content={content}>
      <span className={className ? `${s.hint} ${className}` : s.hint} tabIndex={0}>
        {children}
      </span>
    </HoverCard>
  );
}

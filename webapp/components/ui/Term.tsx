"use client";
import type { ReactNode } from "react";
import { HoverCard } from "./HoverCard";
import s from "./Term.module.css";

// Teaching prose on a term the operator is reading. The ONE mechanism for it: a DOM `title=`
// is unselectable, keyboard-unreachable and dead on touch, so it stays for repeating a string
// the trigger already shows truncated and for nothing else (`webapp/CLAUDE.md`).
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

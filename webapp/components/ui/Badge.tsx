import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./Badge.module.css";

export type BadgeTone = "default" | "accent" | "success" | "danger";

// Small pill label. `tone` maps to a scoped modifier; `default` is the neutral
// pill. Presentational — pair a tone with text that carries the meaning.
export function Badge({
  tone = "default",
  className,
  title,
  children,
}: {
  tone?: BadgeTone;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span className={cx(s.badge, tone !== "default" && s[tone], className)} title={title}>
      {children}
    </span>
  );
}

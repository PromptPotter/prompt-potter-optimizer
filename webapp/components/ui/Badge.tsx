import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./Badge.module.css";

export type BadgeTone = "default" | "accent" | "success" | "danger";

// Small pill label. `tone` maps to a scoped modifier; `default` is the neutral
// pill. Presentational — pair a tone with text that carries the meaning.
export function Badge({
  tone = "default",
  title,
  className,
  children,
}: {
  tone?: BadgeTone;
  title?: string;
  // A host's own geometry for this one badge. Unlayered, so it beats the primitive's
  // defaults whatever order the chunks load in (`webapp/CLAUDE.md` § Stylesheet organization).
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={cx(s.badge, tone !== "default" && s[tone], className)} title={title}>
      {children}
    </span>
  );
}

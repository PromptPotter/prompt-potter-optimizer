import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./Badge.module.css";

export type BadgeTone = "default" | "accent" | "success" | "danger";

export function Badge({
  tone = "default",
  title,
  className,
  children,
}: {
  tone?: BadgeTone;
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={cx(s.badge, tone !== "default" && s[tone], className)} title={title}>
      {children}
    </span>
  );
}

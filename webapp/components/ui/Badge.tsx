import type { ReactNode } from "react";
import type { Tone } from "@/lib/api/types.generated";
import { cx } from "@/lib/cx";
import s from "./Badge.module.css";

export function Badge({
  tone = "quiet",
  title,
  className,
  children,
}: {
  tone?: Tone | undefined;
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={cx(s.badge, s[tone], className)} title={title}>
      {children}
    </span>
  );
}

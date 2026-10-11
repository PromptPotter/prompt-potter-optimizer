import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./Toolbar.module.css";

export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx(s.bar, className)}>{children}</div>;
}

export function ToolbarSep() {
  return <span className={s.sep} aria-hidden="true" />;
}

export function ToolbarSpacer() {
  return <span className={s.spacer} aria-hidden="true" />;
}

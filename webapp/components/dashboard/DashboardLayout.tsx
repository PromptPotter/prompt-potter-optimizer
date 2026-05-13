"use client";
import type { ReactNode } from "react";

// Dashboard layout primitive. Two width tiers — narrow (centered spine
// for header + top strip + diagnostics) and wide (centered but roomier
// for panel grids). Widths live as CSS vars in globals.css
// (`--dash-narrow-max`, `--dash-wide-max`); change a single number in
// one place to retune every dashboard section.
//
// The pattern intentionally avoids one big <DashboardLayout> wrapping
// the whole tree because each section already manages its own internal
// shape (dash-strip is a flex row, dash-grid is a CSS grid, etc.) —
// composing spines around them keeps each section's local layout
// independent.

export function NarrowSpine({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={`dash-spine-narrow${className ? ` ${className}` : ""}`}>{children}</div>;
}

export function WideSpine({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={`dash-spine-wide${className ? ` ${className}` : ""}`}>{children}</div>;
}

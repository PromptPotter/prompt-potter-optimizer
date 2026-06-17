import type { ReactNode } from "react";

// The narrow centred column every dashboard section stacks inside. One layout
// primitive instead of repeating `<div className="dash-spine-narrow">` at each
// section — the spine width is owned in one place.
export function DashSpine({ children }: { children: ReactNode }) {
  return <div className="dash-spine-narrow">{children}</div>;
}

import type { ReactNode } from "react";

export function DashSpine({ children }: { children: ReactNode }) {
  return <div className="dash-spine-narrow">{children}</div>;
}

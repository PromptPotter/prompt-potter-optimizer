"use client";

import type { OriginGap } from "@/lib/api";

export function GapList({ gaps, tone }: { gaps: OriginGap[]; tone: "pending" | "blocked" }) {
  if (gaps.length === 0) return null;
  return (
    <ul className={`origin-gaps origin-gaps--${tone}`}>
      {gaps.map((g) => (
        <li key={g.field}>{g.hint}</li>
      ))}
    </ul>
  );
}

// Per-evaluator glyphs.
// Namespaced display names (e.g. ``fuzzy_matching_source_recall``) fall back
// to their registry stem icon so node-type-bound metrics render with the
// right glyph regardless of which node owns them this round.

import type { ReactNode } from "react";

const COMMON = {
  viewBox: "0 0 24 24",
  fill: "none" as const,
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const ICONS: Record<string, ReactNode> = {
  accuracy: (
    <svg {...COMMON} aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.5" fill="currentColor" />
    </svg>
  ),
  error_rate: (
    <svg {...COMMON} aria-hidden="true">
      <path d="M12 3 2 21h20L12 3z" />
      <path d="M12 10v5" />
      <circle cx="12" cy="18" r="0.6" fill="currentColor" />
    </svg>
  ),
  degraded_rate: (
    <svg {...COMMON} aria-hidden="true">
      <path d="M3 12h4l2-6 4 12 2-6h6" />
    </svg>
  ),
  runtime_failure_rate: (
    <svg {...COMMON} aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="m8 8 8 8M16 8l-8 8" />
    </svg>
  ),
  latency_norm: (
    <svg {...COMMON} aria-hidden="true">
      <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z" fill="currentColor" fillOpacity={0.15} />
    </svg>
  ),
  source_recall: (
    <svg {...COMMON} aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4.3-4.3" />
    </svg>
  ),
  candidate_recall: (
    <svg {...COMMON} aria-hidden="true">
      <path d="m4 7 2 2 4-4" />
      <path d="m4 14 2 2 4-4" />
      <path d="M13 8h8" />
      <path d="M13 15h8" />
    </svg>
  ),
  cache_hit_rate: (
    <svg {...COMMON} aria-hidden="true">
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
      <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
    </svg>
  ),
  retrieval_shortfall: (
    <svg {...COMMON} aria-hidden="true">
      <path d="M12 3v12" />
      <path d="m6 9 6 6 6-6" />
      <path d="M5 21h14" />
    </svg>
  ),
  mean_retrieval_shortfall: (
    <svg {...COMMON} aria-hidden="true">
      <polyline points="3 17 9 11 13 15 21 7" />
      <path d="M14 7h7v7" />
    </svg>
  ),
  pipeline_compactness: (
    <svg {...COMMON} aria-hidden="true">
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="12" r="2.5" />
      <path d="M8 6h6a4 4 0 0 1 4 4v.5" />
      <path d="M8 18h6a4 4 0 0 0 4-4v-.5" />
    </svg>
  ),
  prompt_compactness: (
    <svg {...COMMON} aria-hidden="true">
      <path d="M5 4h14v3" />
      <path d="M9 20h6" />
      <path d="M12 7v13" />
    </svg>
  ),
  output_compactness: (
    <svg {...COMMON} aria-hidden="true">
      <circle cx="12" cy="12" r="8" />
      <path d="M12 7v10" />
      <path d="M14.6 9.3c-.6-.8-1.6-1.3-2.7-1.3-1.7 0-2.8.9-2.8 2.1 0 2.8 5.8 1.5 5.8 4.2 0 1.3-1.2 2.2-3 2.2-1.2 0-2.4-.5-3-1.3" />
    </svg>
  ),
};

const FALLBACK_ICON: ReactNode = (
  <svg {...COMMON} aria-hidden="true">
    <circle cx="12" cy="12" r="8" />
    <path d="M9 12h6M12 9v6" />
  </svg>
);

export function whatifIconFor(displayName: string, registryName: string): ReactNode {
  return ICONS[displayName] ?? ICONS[registryName] ?? FALLBACK_ICON;
}

"use client";
// Wraps a surface that cannot meaningfully fit on a portrait phone
// (the candidates Forest view, the scoring-mask grid, the Optimizer card, HardSamplesHeatmap,
// RoundFileView, etc.). On portrait <768px the children are hidden via
// CSS and a branded "rotate to landscape" card renders in their place.
// On landscape or ≥768px the children render normally.
//
// The `.rotate-prompt-host` / `.rotate-prompt-children` / `.rotate-prompt-card`
// classes in `app/styles/foundation/responsive.css` do the swap; the
// `useIsPortraitPhone()` branch additionally UNMOUNTS the children, because every
// surface this wraps carries an expensive layout pass (large SVG, virtualised table).

import { type ReactNode } from "react";
import { useIsPortraitPhone } from "@/lib/hooks/useMediaQuery";

interface Props {
  children: ReactNode;
  // Operator-readable label for the rotate card. Defaults to a generic
  // line; pass the specific surface name when it adds clarity.
  surfaceName?: string;
}

function RotateIcon() {
  return (
    <svg
      className="rotate-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="5" y="2" width="14" height="20" rx="2.5" />
      <line x1="9" y1="19" x2="15" y2="19" />
      <path d="M17 7l2 2-2 2" />
      <path d="M19 9H9" />
    </svg>
  );
}

export function RotatePrompt({ children, surfaceName }: Props) {
  const isPortraitPhone = useIsPortraitPhone();
  return (
    <div className="rotate-prompt-host">
      {!isPortraitPhone && (
        <div className="rotate-prompt-children">{children}</div>
      )}
      <div
        className="rotate-prompt-card"
        role="status"
        aria-live="polite"
      >
        <RotateIcon />
        <div className="rotate-title">Rotate to landscape</div>
        <div className="rotate-body">
          {surfaceName
            ? `${surfaceName} needs a wider view. Rotate your phone to landscape to see it.`
            : "This view needs a wider screen. Rotate your phone to landscape to continue."}
        </div>
      </div>
    </div>
  );
}

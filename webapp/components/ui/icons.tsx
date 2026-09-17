import type { ReactNode } from "react";

// Stroke glyphs on `currentColor`, so a host's on/hover/disabled ink colours them for free.
// Decorative by construction: an icon-only control carries its own `aria-label`. `size`
// absent leaves the box to the host's CSS.
export function Icon({
  size,
  viewBox = "0 0 24 24",
  strokeWidth,
  children,
}: {
  size?: number;
  viewBox?: string;
  strokeWidth: number;
  children: ReactNode;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox={viewBox}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

interface GlyphProps {
  size?: number;
}

// A branching tree — the forest view.
export const IconTree = ({ size = 15 }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.7}>
    <circle cx="5" cy="12" r="2" />
    <circle cx="18" cy="6" r="2" />
    <circle cx="18" cy="18" r="2" />
    <path d="M7 12h4M11 12l5-5M11 12l5 5" />
  </Icon>
);

// The overflow menu.
export const IconMore = ({ size = 15 }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.7}>
    <circle cx="5" cy="12" r="1" />
    <circle cx="12" cy="12" r="1" />
    <circle cx="19" cy="12" r="1" />
  </Icon>
);

// Dismissal — the one close mark, on every dialog and detail panel.
export const IconClose = ({ size = 16 }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.7}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Icon>
);

// Sweep away — cleaning up empty-stub forks.
export const IconBroom = ({ size = 15 }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.7}>
    <path d="M15 4l5 5" />
    <path d="M13 6l5 5-6.5 6.5a3 3 0 0 1-2 .9L4 19l.6-5.5a3 3 0 0 1 .9-2z" />
  </Icon>
);

// Mixer sliders — tunable mechanisms.
export const IconSliders = ({ size = 14 }: GlyphProps) => (
  <Icon size={size} strokeWidth={2}>
    <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" />
    <path d="M1 14h6M9 8h6M17 16h6" />
  </Icon>
);

export const IconTarget = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="5" />
    <circle cx="12" cy="12" r="1.5" fill="currentColor" />
  </Icon>
);

export const IconWarning = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <path d="M12 3 2 21h20L12 3z" />
    <path d="M12 10v5" />
    <circle cx="12" cy="18" r="0.6" fill="currentColor" />
  </Icon>
);

export const IconPulse = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <path d="M3 12h4l2-6 4 12 2-6h6" />
  </Icon>
);

export const IconBolt = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z" fill="currentColor" fillOpacity={0.15} />
  </Icon>
);

export const IconSearch = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-4.3-4.3" />
  </Icon>
);

export const IconChecklist = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <path d="m4 7 2 2 4-4" />
    <path d="m4 14 2 2 4-4" />
    <path d="M13 8h8" />
    <path d="M13 15h8" />
  </Icon>
);

export const IconDatabase = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <ellipse cx="12" cy="5" rx="8" ry="3" />
    <path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
    <path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
  </Icon>
);

export const IconArrowToBase = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <path d="M12 3v12" />
    <path d="m6 9 6 6 6-6" />
    <path d="M5 21h14" />
  </Icon>
);

export const IconTrendUp = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <polyline points="3 17 9 11 13 15 21 7" />
    <path d="M14 7h7v7" />
  </Icon>
);

export const IconType = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <path d="M5 4h14v3" />
    <path d="M9 20h6" />
    <path d="M12 7v13" />
  </Icon>
);

export const IconCoin = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 7v10" />
    <path d="M14.6 9.3c-.6-.8-1.6-1.3-2.7-1.3-1.7 0-2.8.9-2.8 2.1 0 2.8 5.8 1.5 5.8 4.2 0 1.3-1.2 2.2-3 2.2-1.2 0-2.4-.5-3-1.3" />
  </Icon>
);

export const IconCirclePlus = ({ size }: GlyphProps) => (
  <Icon size={size} strokeWidth={1.6}>
    <circle cx="12" cy="12" r="8" />
    <path d="M9 12h6M12 9v6" />
  </Icon>
);

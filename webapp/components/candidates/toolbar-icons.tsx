// Toolbar glyphs for the candidates card. Inline SVG on `currentColor`, so the
// Chip/SegmentedControl on/hover/disabled states colour them for free — same
// convention as `icons.tsx` and `CopyButton`.
//
// Every one of these is an icon-only control, so each caller MUST pass an
// `ariaLabel` + `title`: the glyph is the affordance, the text is the meaning.

const SVG = {
  width: 15,
  height: 15,
  viewBox: "0 0 24 24",
  fill: "none" as const,
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

// Sequence view — bars.
export const IconBars = () => (
  <svg {...SVG}>
    <line x1="5" y1="20" x2="5" y2="13" />
    <line x1="12" y1="20" x2="12" y2="8" />
    <line x1="19" y1="20" x2="19" y2="4" />
  </svg>
);

// Forest view — a branching tree.
export const IconTree = () => (
  <svg {...SVG}>
    <circle cx="5" cy="12" r="2" />
    <circle cx="18" cy="6" r="2" />
    <circle cx="18" cy="18" r="2" />
    <path d="M7 12h4M11 12l5-5M11 12l5 5" />
  </svg>
);

// Lens — re-project the record under an alternative criterion.
export const IconLens = () => (
  <svg {...SVG}>
    <circle cx="11" cy="11" r="6" />
    <line x1="15.5" y1="15.5" x2="20" y2="20" />
  </svg>
);

// What-If — an ablation you mix yourself.
export const IconFlask = () => (
  <svg {...SVG}>
    <path d="M10 3v6L5 19a1.5 1.5 0 0 0 1.3 2h11.4A1.5 1.5 0 0 0 19 19l-5-10V3" />
    <line x1="9" y1="3" x2="15" y2="3" />
    <line x1="7.5" y1="14" x2="16.5" y2="14" />
  </svg>
);

// Sample set — a fixed grid of samples every candidate is scored over.
export const IconGrid = () => (
  <svg {...SVG}>
    <rect x="4" y="4" width="7" height="7" rx="1" />
    <rect x="13" y="4" width="7" height="7" rx="1" />
    <rect x="4" y="13" width="7" height="7" rx="1" />
    <rect x="13" y="13" width="7" height="7" rx="1" />
  </svg>
);

// The overflow menu.
export const IconMore = () => (
  <svg {...SVG}>
    <circle cx="5" cy="12" r="1" />
    <circle cx="12" cy="12" r="1" />
    <circle cx="19" cy="12" r="1" />
  </svg>
);

// Clean up empty-stub forks.
export const IconBroom = () => (
  <svg {...SVG}>
    <path d="M15 4l5 5" />
    <path d="M13 6l5 5-6.5 6.5a3 3 0 0 1-2 .9L4 19l.6-5.5a3 3 0 0 1 .9-2z" />
  </svg>
);

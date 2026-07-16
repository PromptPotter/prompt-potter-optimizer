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

// Forest view — a branching tree.
export const IconTree = () => (
  <svg {...SVG}>
    <circle cx="5" cy="12" r="2" />
    <circle cx="18" cy="6" r="2" />
    <circle cx="18" cy="18" r="2" />
    <path d="M7 12h4M11 12l5-5M11 12l5 5" />
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

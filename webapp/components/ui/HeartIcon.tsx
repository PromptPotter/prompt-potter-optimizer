import { memo } from "react";

// Minimal 8-bit lava-heart — the brand pixel heart, stripped to HUD size (no
// sideways-laser brackets). Built from crisp unit rects on a 7×6 grid so it
// stays sharp at any scale; `currentColor` lets the caller/theme drive the fill
// (DOOM-orange in dark, the accent in light). `filled=false` dims it to an
// empty slot. Self-contained SVG — no external asset, CSP-safe.
export const HeartIcon = memo(function HeartIcon({
  filled = true,
  size = 11,
}: {
  filled?: boolean;
  size?: number;
}) {
  return (
    <svg
      className="heart-icon"
      width={size}
      height={(size / 7) * 6}
      viewBox="0 0 7 6"
      shapeRendering="crispEdges"
      fill="currentColor"
      fillOpacity={filled ? 1 : 0.18}
      aria-hidden="true"
      focusable="false"
    >
      <rect x="1" y="0" width="2" height="1" />
      <rect x="4" y="0" width="2" height="1" />
      <rect x="0" y="1" width="7" height="1" />
      <rect x="0" y="2" width="7" height="1" />
      <rect x="1" y="3" width="5" height="1" />
      <rect x="2" y="4" width="3" height="1" />
      <rect x="3" y="5" width="1" height="1" />
    </svg>
  );
});

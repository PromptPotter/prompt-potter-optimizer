import { memo } from "react";

const WIDTH = 11;

export const HeartIcon = memo(function HeartIcon({ filled = true }: { filled?: boolean }) {
  return (
    <svg
      className="heart-icon"
      width={WIDTH}
      height={(WIDTH / 7) * 6}
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

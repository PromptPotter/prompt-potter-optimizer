// The PromptPotter mark (v3) — a vessel that is also a violin plot: the
// silhouette is a mirrored density curve, the belly is the mode, and the fill
// level is where the area accumulates.
//
// Replaces the v2 hexagon glyph, which was hand-duplicated in three components
// (login showcase, running-jobs button, About-this-unit). One definition now,
// so the next mark change is one edit.
//
// The artwork is the approved raster (public/brand/mark-pot.png). It is used
// as a CSS mask with `background: currentColor` rather than an <img>, because an
// <img> bakes the ink in: these three surfaces sit on cobalt, on white and on
// near-black, and the glyph has to tint with theme and hover. The mask takes
// only the alpha channel, so colour still comes from the cascade and a
// whitelabel host's accent flows through — BRAND.md principle 5.
//
// NO enclosing circle — a disc lockup was tried and rejected. The browser-tab
// icon solves light/dark chrome with two cuts instead (see app/layout.tsx).

interface Props {
  size?: number;
  className?: string;
}

const MASK = "url(/brand/mark-pot.png) center / contain no-repeat";

export function PotterMark({ size = 24, className }: Props) {
  return (
    <span
      className={className}
      aria-hidden="true"
      style={{
        display: "inline-block",
        flexShrink: 0,
        width: size,
        height: size,
        background: "currentColor",
        WebkitMask: MASK,
        mask: MASK,
      }}
    />
  );
}

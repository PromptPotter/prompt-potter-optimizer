// The PromptPotter mark, drawn as a CSS mask over `currentColor`, never an <img>: an <img> bakes
// the ink in, and a whitelabel accent must flow through (promptpotter-web/BRAND.md principle 4).

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

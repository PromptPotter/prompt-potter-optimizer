// A CSS mask over `currentColor`, never an <img>: a whitelabel accent must flow through (BRAND.md principle 4).

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

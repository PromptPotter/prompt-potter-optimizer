import type { ReactNode, SVGProps } from "react";

// Decorative-by-default icon wrapper. Replaces vanilla's hideDecorativeSvgs()
// boot pass: every <Icon> sets aria-hidden="true" unless `label` is given,
// in which case it becomes role="img" with aria-label.
export function Icon({
  label,
  children,
  ...rest
}: SVGProps<SVGSVGElement> & { label?: string; children: ReactNode }) {
  if (label) {
    return (
      <svg role="img" aria-label={label} {...rest}>
        {children}
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" {...rest}>
      {children}
    </svg>
  );
}

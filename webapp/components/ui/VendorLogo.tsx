"use client";
// The mark for who TRAINED a model, keyed by `lib/format.ts::vendorOf`. Never fetch a logo at
// runtime: OpenRouter's icon path answers a miss with HTTP 200 HTML, breaking silently.
import type { CSSProperties } from "react";
import { cx } from "@/lib/cx";
import { seriesVar } from "@/lib/theme";
import { VENDOR_MARKS } from "./vendor-marks.generated";
import s from "./VendorLogo.module.css";

// `<use href>` resolves within the DOCUMENT: unmount `VendorSprite` (in `AppShell`) and every mark
// draws nothing.
const SYMBOL = (vendor: string) => `pp-vendor-${vendor}`;

export function VendorSprite() {
  return (
    <svg className={s.sprite} aria-hidden="true" focusable="false">
      {Object.entries(VENDOR_MARKS).map(([vendor, mark]) =>
        mark.paths ? (
          <symbol key={vendor} id={SYMBOL(vendor)} viewBox="0 0 24 24">
            {mark.paths.map((d) => (
              <path key={d} d={d} fill="currentColor" />
            ))}
          </symbol>
        ) : null,
      )}
    </svg>
  );
}

export function vendorLabel(vendor: string): string {
  return VENDOR_MARKS[vendor]?.label ?? vendor;
}

// Not one neutral grey: two unknown vendors sharing an initial would read as one mark.
function fallbackTint(vendor: string): string {
  let hash = 0;
  for (let i = 0; i < vendor.length; i += 1) hash = (hash * 31 + vendor.charCodeAt(i)) | 0;
  return seriesVar(Math.abs(hash));
}

// `models` are the ids this mark STANDS IN FOR: the sidebar row shows it instead of the model text.
export function VendorLogo({
  vendor,
  models,
  className,
}: {
  vendor: string;
  models?: readonly string[];
  className?: string;
}) {
  const mark = VENDOR_MARKS[vendor];
  const label = vendorLabel(vendor);
  const name = models && models.length > 0 ? `${label} — ${models.join(", ")}` : label;
  return (
    <span
      className={cx(s.logo, className)}
      style={{ "--vendor-tint": mark?.tint ?? fallbackTint(vendor) } as CSSProperties}
      role="img"
      aria-label={name}
    >
      {mark?.paths ? (
        <svg className={s.glyph} aria-hidden="true" focusable="false">
          <use href={`#${SYMBOL(vendor)}`} />
        </svg>
      ) : (
        <span aria-hidden="true">{(label[0] ?? "?").toUpperCase()}</span>
      )}
    </span>
  );
}

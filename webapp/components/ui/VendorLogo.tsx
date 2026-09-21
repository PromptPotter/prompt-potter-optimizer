"use client";
// The mark for a model's VENDOR — the org that TRAINED it, keyed by the namespace
// `lib/format.ts::vendorOf` reads off its id. One tier up from
// `components/account/providers.tsx::ProviderIcon`, which marks who you signed IN with; this
// one marks who made the model a campaign ran on.
//
// SPRITE, not a repeated inline path. The geometry is declared ONCE by `VendorSprite` as a
// `<symbol>` per vendor, and every mark on screen is a `<use>` pointing at it. The sidebar
// lists every campaign the workspace holds, so an inline `<path>` would stamp the same ~2KB of
// DeepSeek geometry into the DOM once per row — the duplication `<use>` exists to remove.
//
// The geometry, its upstream and its licence are `vendor-marks.generated.ts`, written by
// `scripts/build_vendor_marks.py` off the `simple-icons` devDependency plus the checked-in
// exceptions in `webapp/assets/vendor-marks/`. Nothing is fetched at RUNTIME: OpenRouter's API
// serves no logo (`/api/v1/models` and `/api/v1/providers` both carry none), and its
// undocumented static icon path answers a MISS with HTTP 200 `text/html` — a mark that breaks
// with nothing anywhere to say so.
//
import type { CSSProperties } from "react";
import { cx } from "@/lib/cx";
import { seriesVar } from "@/lib/theme";
import { VENDOR_MARKS } from "./vendor-marks.generated";
import s from "./VendorLogo.module.css";

// `<use href>` resolves within the DOCUMENT, so the sprite has to be mounted for a mark to
// draw. One mount, in `AppShell` — and the prefix is here rather than at either call site so
// the two cannot drift apart into a page of silently empty marks.
const SYMBOL = (vendor: string) => `pp-vendor-${vendor}`;

// Declares every vendor symbol. Mounted ONCE, near the root. It draws nothing itself.
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

// How a vendor reads when it is spelled out. An unknown namespace answers with itself — the
// set of vendors is open, so there is no total map to be had and a blank would lose the fact.
export function vendorLabel(vendor: string): string {
  return VENDOR_MARKS[vendor]?.label ?? vendor;
}

// The ink for a vendor nobody has drawn yet. NOT one neutral grey: `inception` and
// `inclusionai` are two live brands sharing an initial, so a shared fallback ink makes them one
// mark — the exact collision this component exists to prevent. The slot is stable per name and
// comes from the categorical palette the chart layer already owns (tokens.css names "a model in
// Activity" as one of its consumers), so it flips with the theme and no second palette is born.
function fallbackTint(vendor: string): string {
  let hash = 0;
  for (let i = 0; i < vendor.length; i += 1) hash = (hash * 31 + vendor.charCodeAt(i)) | 0;
  return seriesVar(Math.abs(hash));
}

// `models` are the ids this mark STANDS IN FOR. On the sidebar row the mark replaces the model
// text outright, so it carries the reading and earns an accessible name rather than
// `aria-hidden` — it is not decoration there.
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

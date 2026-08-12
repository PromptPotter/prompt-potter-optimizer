// Mobile-budget alarm. Two rules the browser cannot tell you about until an
// operator opens the app on a phone, by which point the layout is already
// broken. Both are static: no browser, no dependency, same runner as
// `css-breakpoints.test.ts`.
//
// 1. Every `font-size` reads a `--text-*` token. Seventeen raw px values and a
//    second `rem` scale had drifted apart across 29 files; nothing chose
//    between 10.5, 11 and 11.5, and the sub-11px tier is unreadable on glass.
// 2. No rule DEMANDS more width than the narrowest real phone can give it.
//    `max-width:100%` does not rescue a larger `min-width` — the min wins, and
//    that is exactly how `.hs-block` forced the page into horizontal scroll.
//
// Scope note: `max-width` is never flagged (a cap cannot overflow), and `width`
// is flagged only without an escape hatch in the same declaration block.

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const STYLES_DIR = path.resolve(__dirname, "../../app/styles");
const COMPONENTS_DIR = path.resolve(__dirname, "../../components");

// The narrowest phone we hold ourselves to (iPhone SE-class portrait), minus
// the content gutter the shell keeps at that width.
const CONTENT_BUDGET_PX = 320;

function cssFiles(): { file: string; text: string }[] {
  const out: { file: string; text: string }[] = [];
  const walk = (dir: string, match: (f: string) => boolean) => {
    for (const entry of readdirSync(dir, { recursive: true }).map(String)) {
      if (!match(entry)) continue;
      out.push({
        file: path.relative(path.resolve(__dirname, "../.."), path.join(dir, entry)),
        text: readFileSync(path.join(dir, entry), "utf-8"),
      });
    }
  };
  walk(STYLES_DIR, (f) => f.endsWith(".css"));
  walk(COMPONENTS_DIR, (f) => f.endsWith(".module.css"));
  return out;
}

// Inline `style={{fontSize: 11}}` is a font-size the CSS files never see, so the
// scale rule above was enforced on 29 stylesheets and silently unenforced on 25
// TSX sites. Same rule, the other half of the surface.
function tsxFiles(): { file: string; text: string }[] {
  return readdirSync(COMPONENTS_DIR, { recursive: true })
    .map(String)
    .filter((f) => f.endsWith(".tsx"))
    .map((f) => ({
      file: path.join("components", f),
      text: readFileSync(path.join(COMPONENTS_DIR, f), "utf-8"),
    }));
}

describe("mobile budget", () => {
  it("sizes every font from the --text-* scale", () => {
    // The one sanctioned literal: iOS Safari auto-zooms on focus below 16px,
    // so a touch text input pins its own floor. A token that happens to equal
    // 16 today would take the zoom suppressor with it on the next retune.
    const IOS_FLOOR = /font-size:\s*(?:16px|max\(\s*16px\s*,\s*1em\s*\))/;
    const RAW = /font-size:\s*[0-9.]+(?:px|rem|em)/g;

    const violations: string[] = [];
    for (const { file, text } of cssFiles()) {
      for (const line of text.split("\n")) {
        for (const hit of line.matchAll(RAW)) {
          if (IOS_FLOOR.test(hit[0])) continue;
          // `em` is relative to the parent on purpose — a different mechanism,
          // not a second scale.
          if (/[0-9.]em$/.test(hit[0]) && !/rem$/.test(hit[0])) continue;
          violations.push(`${file}: ${hit[0]}`);
        }
      }
    }
    // `fontSize: 0` hides text (a swatch, a spacer) — a technique, not a tier.
    const INLINE = /fontSize:\s*(\d*\.?\d+)/g;
    for (const { file, text } of tsxFiles()) {
      for (const hit of text.matchAll(INLINE)) {
        if (Number(hit[1]) !== 0) violations.push(`${file}: ${hit[0]}`);
      }
    }
    expect(
      violations,
      `Raw font-size outside the scale:\n  ${violations.join("\n  ")}\n` +
        `Use a --text-* token from foundation/tokens.css ` +
        `(inline: fontSize: "var(--text-sm)").`,
    ).toEqual([]);
  });

  it("demands no more width than the narrowest phone gives", () => {
    // Split on `}` so an escape hatch counts only inside its own block.
    const BLOCK = /[^{}]*\{[^{}]*\}/g;
    const MIN_W = /min-width:\s*([0-9.]+)px/g;
    const WIDTH = /(?<!max-|min-)width:\s*([0-9.]+)px/g;

    const violations: string[] = [];
    for (const { file, text } of cssFiles()) {
      for (const block of text.match(BLOCK) ?? []) {
        const escapes = /max-width:\s*100%|width:\s*min\(|width:\s*clamp\(/.test(block);
        for (const m of block.matchAll(MIN_W)) {
          // A min-width is never rescued by max-width:100% — it wins outright.
          if (Number(m[1]) > CONTENT_BUDGET_PX) {
            violations.push(`${file}: min-width:${m[1]}px`);
          }
        }
        if (escapes) continue;
        for (const m of block.matchAll(WIDTH)) {
          if (Number(m[1]) > CONTENT_BUDGET_PX) {
            violations.push(`${file}: width:${m[1]}px (no max-width:100%)`);
          }
        }
      }
    }
    expect(
      violations,
      `Rules wider than the ${CONTENT_BUDGET_PX}px phone budget:\n  ${violations.join("\n  ")}\n` +
        `Wrap in min(…, 100%), or pair a fixed width with max-width:100%.`,
    ).toEqual([]);
  });
});

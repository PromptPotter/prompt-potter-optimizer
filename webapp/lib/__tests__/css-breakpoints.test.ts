// Breakpoint sprawl alarm. Asserts every `@media (min-width|max-width: NNN)`
// value across `webapp/app/styles/**/*.css` is on the canonical breakpoint list.
//
// The list mirrors the responsive tokens in `:root` (--bp-sm / rotate /
// md) plus a handful of pre-foundation values that the sweep will migrate. The test fires when a developer adds a new breakpoint that isn't
// on the list — forcing them to either reuse an existing token or update
// this allowlist deliberately. (Container queries are out of scope; they
// reflow on container width, not viewport, and 4 distinct values already
// ship — the sweep can consolidate them later.)
//
// Why a Vitest assertion rather than stylelint: the project's CI gate is
// vitest + tsc + lint + build. Adding stylelint is a meaningful new lane;
// a single ~30-line test in the existing scope is cheaper and gives the
// same signal.

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Canonical list. New entries require deliberate review — every addition
// is a content-driven breakpoint decision (see plan: nested-humming-goose).
const ALLOWED_BREAKPOINTS = new Set<number>([
  640, // --bp-sm   (large phone)
  767, // --bp-rotate - 1 (rotate-prompt cutoff)
  768, // --bp-rotate
  880, // --bp-md   (small tablet / landscape phone)
  960, // pre-foundation: the candidates card's chart/mask reflow
]);

const STYLES_DIR = path.resolve(__dirname, "../../app/styles");

const MEDIA_BREAKPOINT_RE =
  /@media[^{]*\(\s*(?:min|max)-width\s*:\s*(\d+)px\s*\)/g;

function allStylesheets(): string {
  return readdirSync(STYLES_DIR, { recursive: true })
    .map(String)
    .filter((f) => f.endsWith(".css"))
    .map((f) => readFileSync(path.join(STYLES_DIR, f), "utf-8"))
    .join("\n");
}

describe("stylesheet breakpoint set", () => {
  it("uses only canonical breakpoint values", () => {
    const css = allStylesheets();
    const found = new Set<number>();
    for (const match of css.matchAll(MEDIA_BREAKPOINT_RE)) {
      found.add(Number(match[1]));
    }
    const violations = [...found].filter((n) => !ALLOWED_BREAKPOINTS.has(n));
    expect(
      violations,
      `Non-canonical @media breakpoints in app/styles: ${violations.join(", ")}. ` +
        `Either reuse an existing token (--bp-sm/rotate/md) or add ` +
        `the value to ALLOWED_BREAKPOINTS in css-breakpoints.test.ts with a ` +
        `comment explaining why.`,
    ).toEqual([]);
  });
});

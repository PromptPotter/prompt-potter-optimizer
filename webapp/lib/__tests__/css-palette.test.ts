// Palette sprawl alarm — the sibling of `css-breakpoints.test.ts`, and the check that keeps a
// consolidation from silently un-consolidating. A colour written as a literal is a declaration
// nobody can find: it does not flip theme, it drifts from the token that means the same thing,
// and a grep for the concept misses it. Both halves of that failure have shipped here — five
// spellings of `#e74c3c` beside a `--color-danger` that is a DIFFERENT red, and three parallel
// categorical palettes of which two never flipped theme.
//
// The rule: a hue is declared in `foundation/`, and everything else names it.

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const WEBAPP = path.resolve(__dirname, "../..");

// Where a literal is CORRECT, each with the reason. Anything not on this list is a violation.
const ALLOWED = new Map<string, string>([
  ["app/styles/foundation/tokens.css", "the declaration site"],
  ["app/styles/foundation/themes.css", "the per-theme override site"],
  ["app/styles/domains/login.css", "surface-scoped palette, BRAND.md-owned (--ls-*)"],
  ["app/layout.tsx", "<meta theme-color> cannot read a custom property"],
  ["components/account/providers.tsx", "third-party brand mark"],
  ["components/login/AuthCore.tsx", "third-party brand mark"],
  ["components/brand/SurfaceFavicon.tsx", "favicon SVG, painted outside the document cascade"],
  ["components/ui/ErrorBoundary.tsx", "renders when the app is broken — must not depend on the stylesheet"],
]);

// Only a numeric rgb triple counts — `rgba(var(--x),…)` and `rgba(${a[0]},…)` are a token and a
// resolved read, which is the shape being asked for.
const HEX_OR_RGBA = /#[0-9a-fA-F]{3,8}\b|rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+[^)]*\)/g;

// A GREY is not a palette hue: white lifts, black recedes, and neither carries meaning. Anything
// with a hue does, and a hue is what has to be declared once.
function isNeutral(literal: string): boolean {
  const rgba = literal.match(/[\d.]+/g);
  if (literal.startsWith("rgb")) return rgba != null && rgba[0] === rgba[1] && rgba[1] === rgba[2];
  const hex = literal.slice(1);
  const wide = hex.length > 4;
  const ch = (i: number) => (wide ? hex.slice(i * 2, i * 2 + 2) : hex[i]!.repeat(2));
  return ch(0) === ch(1) && ch(1) === ch(2);
}

function sourceFiles(dir: string, exts: string[]): string[] {
  return readdirSync(path.join(WEBAPP, dir), { recursive: true })
    .map(String)
    .filter((f) => exts.some((e) => f.endsWith(e)) && !f.includes("__tests__"))
    .map((f) => path.posix.join(dir, f.split(path.sep).join("/")));
}

describe("colour literals", () => {
  it("live only in foundation/ and the named exceptions", () => {
    const files = [
      ...sourceFiles("app", [".css", ".tsx"]),
      ...sourceFiles("components", [".tsx", ".ts", ".css"]),
    ];
    const violations: string[] = [];
    for (const rel of files) {
      if (ALLOWED.has(rel)) continue;
      const text = readFileSync(path.join(WEBAPP, rel), "utf-8");
      for (const line of text.split("\n")) {
        for (const m of line.match(HEX_OR_RGBA) ?? []) {
          if (!isNeutral(m)) violations.push(`${rel}: ${m}`);
        }
      }
    }
    expect(
      violations,
      `Hardcoded colours outside app/styles/foundation:\n  ${violations.join("\n  ")}\n` +
        `Declare the hue as a token in foundation/tokens.css (with a themes.css override if it ` +
        `must flip) and name it here — or add the file to ALLOWED with the reason it cannot.`,
    ).toEqual([]);
  });

  it("gives every status/annotation hue a light-theme answer", () => {
    const foundation = (f: string) =>
      readFileSync(path.join(WEBAPP, "app/styles/foundation", f), "utf-8");
    const tokens = foundation("tokens.css");
    const light = foundation("themes.css");
    // The trap this fires on: `--color-new` shipped as three raw literals in a component and was
    // the ONE member of its family with no light value, so it painted dark-mode azure on paper.
    for (const hue of ["success", "warn", "new"]) {
      expect(tokens, `--color-${hue} is not declared`).toContain(`--color-${hue}:`);
      expect(light, `--color-${hue} has no light-theme value`).toContain(`--color-${hue}:`);
      for (const fill of ["bg", "border"]) {
        expect(tokens, `--color-${hue}-${fill} must derive from the family`).toContain(
          `--color-${hue}-${fill}:rgba(var(--color-${hue}-rgb),var(--status-`,
        );
      }
    }
    // Danger is deliberately theme-invariant — tokens.css says so. Pinned so a future light
    // override is a decision someone makes here, not a drift.
    expect(light).not.toContain("--color-danger:");
  });

  it("declares one categorical palette, and every slot of it", () => {
    const tokens = readFileSync(path.join(WEBAPP, "app/styles/foundation/tokens.css"), "utf-8");
    const theme = readFileSync(path.join(WEBAPP, "lib/theme.ts"), "utf-8");
    const slots = Number(theme.match(/const SERIES_SLOTS = (\d+)/)?.[1]);
    expect(slots, "theme.ts::SERIES_SLOTS is the slot count seriesColor wraps at").toBeGreaterThan(0);
    for (let i = 1; i <= slots; i++) {
      expect(tokens, `--chart-series-${i} is missing, so seriesColor() paints nothing`).toContain(
        `--chart-series-${i}:`,
      );
    }
  });
});

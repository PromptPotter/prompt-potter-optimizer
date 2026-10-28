# webapp/app — CLAUDE.md

## Stylesheet organization (cascade order is load-bearing)

**The barrel order IS the cascade** — `app/styles/index.css` is the only stylesheet `app/layout.tsx` imports, inlined into one sheet: `foundation/` first, `domains/` (one file per feature, `@media` co-located) after, the cross-cutting tail files last. A moved rule keeps its relative order; never reintroduce a `globals.css`. **Every `components/ui/*.module.css` sits in `@layer ui`** (the `*` reset in `@layer ui.reset`), so a call-site class beats a primitive default.

**Type, space and colour come from tokens, never a literal** (`--text-*`, `--space-*`, hues in `foundation/tokens.css`; greys exempt), and a breakpoint reuses a `--bp-*` token. **Nothing enforces this** — read it in the diff. Exceptions: `16px` on a touch text input (iOS zoom), sub-4px hairlines, `fontSize: 0`. A categorical ink comes from `theme.ts` (`seriesColor` for `<canvas>`, `seriesVar` elsewhere); only a `<canvas>` needs `getCss`.

**A field is skinned once, in `foundation/base.css`**; a surface sizes it, never re-declares its border, radius, padding or colours. **A box states no width or height of its own** — `.box-grid`, `--box-min`, `--box-scroll-max`, `--chart-h`; `--reading-measure` caps prose only.

**`overflow:hidden` on a wrapper that can be outgrown silently DELETES content** — use `overflow-x:auto`, and `min(…,100%)` for a fixed width. **A `viewBox`'d SVG at `width:100%` scales until labels collide** — give it intrinsic width in a scrolling wrapper, or shrink the geometry (`forest-layout::Density`), never the scale.

State classes compose with `cx()` (`lib/cx.ts`), never a template string.

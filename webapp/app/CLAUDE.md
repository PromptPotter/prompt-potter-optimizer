# webapp/app — CLAUDE.md

**The barrel order IS the cascade**: `app/styles/index.css` is the only stylesheet `app/mount.tsx` imports (`foundation/`, then `domains/`, then the cross-cutting tail); never a `globals.css`. <!-- probe: git grep -cE "\.css" -- webapp/app/mount.tsx <= 1 --> Every `components/ui/*.module.css` sits in `@layer ui`, so a call-site class beats a primitive default.

**Type, space and colour come from tokens, never a literal** (greys exempt; `16px` on a touch text input, sub-4px hairlines); a categorical ink comes from `theme.ts`. **A field is skinned once, in `foundation/base.css`; a box states no width or height of its own.**

**`overflow:hidden` on a wrapper that can be outgrown silently DELETES content**: use `overflow-x:auto`. A `viewBox`'d SVG at `width:100%` scales until labels collide: give it intrinsic width in a scrolling wrapper. State classes compose with `cx()`, never a template string.

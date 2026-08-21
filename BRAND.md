# BRAND.md — Visual identity

> Single source of truth for **design** across this repo, the webapp, and the sibling marketing repo `promptpotter-web`. Copy register and language rules live in [`VOICE.md`](VOICE.md). Tokens live in `webapp/app/styles/foundation/tokens.css`.

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. CLI-first today; the file tree (per-cycle `dashboard.json`, `rounds/`, `log.md`) is the operator's primary surface, with a read-only webapp served at the **domain root** (Next.js static export at `webapp/out/`, source at `webapp/`) polling `dashboard.json`. M12 promotes the webapp to a full control plane while keeping the file tree authoritative. Whitelabel distribution is a stated goal — every brand element must be themable.

The public **pre-release landing + waitlist site** is a *separate* Astro repo at `promptpotter-web` (sibling of this repo). Both repos share the **light-register elegant palette** and the logo/mark set. This document covers the CLI and the webapp directly; marketing copy, CTAs, and Astro page structure live in the sibling repo and read the same tokens.

## Users — theme maps to audience

The theme picker is the audience selector.

- **Light theme = primary (central register).** Editorial-cobalt. The default the webapp loads in, and the register the brand identifies with publicly. Buyers, product managers, founders, team leads — and operators by default — land here.
- **Dark theme = alternate operator mode.** DOOM/lava/galactic. ML engineers and SWEs who opt into the dense terminal-adjacent surface during deep optimization work. Distinct *register* — not a recolor. Orange lives here.

Same components, same Potter portrait, same wordmark — but **theme change ≠ recolor**. Switching theme swaps palette, copy register, density, motion budget, and the underlying framing all at once. Tokens drive the swap; component structure stays identical. The **light editorial-cobalt theme is the brand's central register** — what the webapp loads in, what the marketing site uses, what every screenshot in `docs/` should show unless the doc is specifically about the operator's dark mode.

## Aesthetic Direction

- **References**:
  - **TypeDB.com (Vaticle)** — clean modern data-product site, strong typography, dual themes treated as equals. The structural reference for the webapp shell.
  - **Editorial cobalt** — the light theme's register. Cobalt `#090C9B` accent, oxblood `#55251D` depth, taupe `#C5AFA4` surface tint, olive `#696047` muted, on warm-bone `#F5F1EA` paper. Reads as research-grade publication, not as marketing brochure. Editorial calm at moderate density. **No orange anywhere on the light surface.**
  - **DOOM / lava-surface-planet, with galactic depth** — the dark theme's register. Molten oranges and ambers on near-black, deep galactic blacks at the back. Sideways energy beams, never downward sparkles. Orange is the operator's color.
  - **(Sibling repo)** `promptpotter-web` is the public landing on the same editorial-cobalt palette + logo set. Visual tokens align between both repos.
- **Themes** — both polished, both first-class. Neither is a port.
  - *Light theme*: editorial-cobalt — warm-bone `#F5F1EA` paper, taupe `#C5AFA4` tints, cobalt `#090C9B` accent, oxblood `#55251D` depth, olive `#696047` muted, on near-black `#0a0a0a` ink. Editorial, daytime, buyer's register. Palette + logo set shared with `promptpotter-web`. No orange.
  - *Dark theme*: near-black (`#0d0d0d`) with amber/molten-orange accent (`#f59e0b`, `#ea580c`). Operator's home. DOOM/lava/galactic energy — intentionally the nerdy counterpart to light's elegance, not a recolor of it.
- **Typography**: System sans (`-apple-system, "Segoe UI"`) + SF Mono / Menlo for data, IDs, payloads, formulas. 15px base. Hierarchy via weight and size, not font variety.
- **Mark — the vessel** (primary, v3): an abstract black-and-white glyph of a pot whose silhouette is also a violin plot. It is a real fusion, not a visual pun — a thrown pot is a surface of revolution, so its profile *is* a mirrored density curve. The belly is the mode, the fill level is where the area accumulates, and the whisker rising from the mouth is the box plot's upper whisker. The artwork is **raster**, at [`webapp/public/brand/mark-pot.png`](webapp/public/brand/mark-pot.png), rendered through one shared component, `webapp/components/brand/PotterMark.tsx`. **Interim** — not yet the official logo, because the `PP` lockup is still missing.
- **Mark — the Potter portrait** (community): the pixel-art Potter. **Not retired, reassigned.** It is the *insider / community* mark now — README, community channels, the site's long-form illustration — never product chrome. Where it does appear it still reads as **Superman-tier powerful**, not whimsical; lasers shoot **sideways** (left/right from his POV), never down. `PromptPotter` always renders as literal text in branded imagery. Naming conventions for the mark in copy → [`VOICE.md`](VOICE.md).
- **Motion**: Subtle and purposeful. Live-data transitions (status dot pulse, progress fills, polling refresh) are the only animations that earn their frame budget. No scroll-jacking, no parallax, no decorative motion.

## Mark lineage

**This repo tracks only the artwork it renders.** A committed image sits in every clone forever,
so a superseded mark is read back out of git history rather than parked in the tree: the v2
hexagon is `webapp/public/brand/potter-mark.svg` at any commit before v3, and the v1 portrait is
a 64² cut of the still-tracked [`docs/assets/wizard.jpg`](docs/assets/wizard.jpg). The **master
artwork lives once, in `promptpotter-web/public/`** — everything under `webapp/public/brand/` is
a derived cut of it.

| | Mark | From | Status |
|---|---|---|---|
| **v1** | **The Potter portrait** — pixel-art mascot, dark/orange register | 2026-04-15 | **Reassigned**, not retired → community / insider mark |
| **v2** | **The hexagon** — hex outline, triangle, baseline, side ticks | 2026-06-01 | Superseded |
| **v2.5** | **🏺 emoji favicon** — inline data-URI placeholder in `app/layout.tsx` | — | Superseded |
| **v3** | **The vessel** — violin plot drawn as a thrown pot | 2026-08-09 | **Current**, interim pending the `PP` lockup |

Two things worth remembering from the v2 era: the hexagon **never reached `promptpotter-web` at all**
— it was webapp-only, so the marketing site and the live unit showed different marks for two months;
and the emoji placeholder was already an amphora, reaching for what v3 now is deliberately.

**The shipped artwork is the raster, and fidelity to it is the rule.** A vector redraw was trialled
and rejected — see *Unshipped* below. The mark is used as a **CSS mask painted with
`currentColor`**, never as an `<img>`: an `<img>` bakes the ink in, and the in-app surfaces sit on
cobalt, on white and on near-black, so the glyph has to tint with theme and hover.

Two cuts beyond the mask itself, neither of them a redesign:

- **Tab pair** — `tab-icon-pot-32.png` (ink) and `tab-icon-pot-32-dark.png` (bone), selected by
  `media="(prefers-color-scheme: …)"` on the `<link>`. Raster cannot carry the media query internally
  the way an SVG favicon can, hence two files. In the app they are the **pre-paint** icon only:
  `SurfaceFavicon.tsx` repaints the tab from the 128² master onto a ground the hostname picks — a gold
  disc on localhost, a near-black rounded square on the deployed unit, the mark flipping ink/gold to
  sit on each and breaking out of the shape at 92% of the tile. The site leaves the pair untinted, so
  a session's three tabs are three icons.
- **App icon** — `app-icon-pot-512.png`, **opaque on bone paper**. A launcher composites an installed icon
  onto a surface we do not control, so a transparent mark would vanish on a dark one.

**Known gap, open for follow-up:** at 16px the artwork's wall renders at 20–40% alpha and the whisker
all but disappears — only the filled belly stays solid. Measured, not guessed. Fixing it means
optically thickening the artwork at small sizes; until that work happens the tab icon reads as a
filled pot rather than a drawn one.

*Attempted 2026-08-09 and rejected.* A thickened cut — inner wall rebuilt as an inset from the outer
contour, fill dropped onto the widest row — is genuinely legible at 16px where the artwork is not,
but it no longer reads as the artwork. Two findings worth keeping for the next attempt:

- **Thicker is not monotonically better.** The hollow is what reads as *a vessel with a level in it*.
  A wall heavy enough to close it turns the mark into a dark blob with a keyhole — so the useful
  range is narrow, and it must be judged on a 16px render, never a poster-size one.
- **Wall thickness and fill height are one control, not two.** Dropping the fill buys back the open
  area a thicker wall costs; changing either alone hits the blob failure sooner.

Sample cuts were not kept — regenerate from the source rather than reviving them, since the whole
reason they were dropped is that they drifted from it.

**Unshipped — the vector redraw (v3-b).** A traced vector of the artwork at 98.7% IoU, kept at
[`docs/assets/marks/v3b-vector-redraw.svg`](docs/assets/marks/v3b-vector-redraw.svg) as the
starting point for the refinement pass, not as a current option. Two cuts were derived from it
and neither was kept, because in both cases the finding is worth more than the file: the
**optically-thickened** one is what actually reached the screen, and it reads visibly heavier
than the artwork; the **disc lockup** was a workaround for the light/dark tab problem that the
tab pair now solves. **Do not put the mark in a circle.**

## Asset inventory — every brand graphic, and what it actually is

**Files are named for what they are and where they go**, not for the legacy platform filenames
(`favicon.ico`, `apple-touch-icon.png`). Those names say nothing about the artwork, and "favicon" is
a 1999 Internet-Explorer term for *favorites icon* that survives only as a convention. We pay one
small cost for that: a bare request to `/favicon.ico` from a crawler that ignores `<link>` tags gets
a 404. Our own pages always carry the link tags, so nothing user-facing depends on it.

**Naming pattern: `<what-it-is>-pot[-size][-dark]`.** The role leads, because that is what you are
looking for when you go hunting; `pot` names the subject; size and `-dark` separate cuts of the same
thing. So `app-icon-pot-512.png` reads as *the app icon, of the pot, at 512* — no platform trivia required.

**Every cut is sized to its largest render and palette-encoded.** A committed image is permanent
in a way source is not — deleting it later shrinks the checkout and nothing else — so a cut ships
at the size something actually asks for it and at the colour depth a two-tone glyph needs, never
at whatever the master happens to be. The mark is masked at ≤26 px, so it is 128²; the app icon is
512² because a launcher asks for that. Re-cut from the master with `sharp`; do not copy it across.

### The mark (v3) — `promptpotter-web/public/`

| File | What it is | Where it shows |
|---|---|---|
| `mark-pot.png` | The mark. Ink on transparent, 512². **The alpha channel is the mark** — every on-page use masks this file and paints it with `currentColor`. | nav, footer (via `BrandMark.astro`) |
| `tab-icon-pot-32.png` | 32², ink | browser tab, light chrome |
| `tab-icon-pot-32-dark.png` | 32², bone | browser tab, dark chrome |
| `tab-icon-pot.ico` | 16 + 32, ink, legacy container | browser tab, engines that ignore `media=` on icons |
| `share-card-pot.png` | 1200×630 card: mark + wordmark on bone, cobalt rule at the top | the link-unfurl image (`og:image` / `twitter:image`) on every page |
| `app-icon-pot-512.png` | 512², **opaque on bone** — a home screen or launcher composites onto a ground we do not control, so this cut is never transparent | iOS home screen |

### The mark (v3) — `webapp/public/brand/`

| File | What it is | Where it shows |
|---|---|---|
| `mark-pot.png` | same artwork, cut to **128²** — nothing paints it above 32 px, so DPR sets the ceiling, not print | login eyebrow, running-jobs button, About-this-unit (via `PotterMark.tsx`), and the tab icon `SurfaceFavicon.tsx` recolours |
| `tab-icon-pot-32.png` / `-dark.png` | 32² ink / bone | the unit's tab before `SurfaceFavicon` repaints it — and with JS off, instead of it |
| `app-icon-pot-512.png` | same cut as the site's | Web App Manifest — the installed-app / launcher icon |

**One app icon, both jobs.** The iOS home-screen icon and the PWA launcher icon are the same
picture at the same job — an opaque square on an uncontrolled ground — so it is one cut, at 512²,
in both repos. iOS downscales it without complaint; there is no separate 180 cut. The two files
are not byte-identical: this repo's copy is palette-encoded per the cut rule above, which is an
encoding choice, not a second design.

### The Potter portrait (v1) — community mark, not product chrome

| File | What it is | Where it shows |
|---|---|---|
| `promptpotter-web/public/wizard.jpg` | Full-size pixel-art portrait, 960² | the Mechanics illustration |
| `promptpotter-web/public/wizard-64x64.png` | 64² portrait | currently unreferenced; kept as the community mark |
| `prompt-potter-optimizer/docs/assets/wizard.jpg` | same portrait | README |

### Wordmarks and email art

| File | What it is | Where it shows |
|---|---|---|
| `promptpotter-web/public/promptpotter-wordmark.png` | Ember pixel wordmark on black — **dark operator register** | not currently referenced |
| `promptpotter-web/public/email/wordmark.jpg` | Ember wordmark, email cut | waitlist-email header banner |
| `promptpotter-web/public/email/card-*.png`, `thankyou.jpg` | Email body art | the waitlist confirmation email |

Page illustrations are **not** brand marks and are not governed here: `hero-engine.png`,
`hero-loop.png`, `potter-loop-failing-to-verified.png`, `spotlight-bg.png`.

## Anti-references — what this must NOT look like

- **Generic AI/SaaS** — no purple-to-pink gradients, no Inter, no rounded-cards-on-rounded-cards, no ChatGPT-wrapper hero.
- **Over-designed agency portfolio** — no parallax overload, no animation for its own sake, no scroll-jacking, no "wow" over usability.
- **Crude/unstyled dev dashboard** — the original "ugly read-only preview" aesthetic is placeholder. Raw HTML tables and missing hierarchy are not the brand.
- **Heavy enterprise legacy UI** — no tiny gray text, no cluttered toolbars, no nested tabs. No Salesforce/SAP energy.

(Copy-register anti-references — the friendly-wizard trap — live in [`VOICE.md`](VOICE.md).)

## Accessibility & Inclusion

The brand is explicitly **anti-nerdy / pro-accessibility** — accessibility is a positioning feature, not a compliance task.

- **WCAG 2.2 AA** minimum on every surface; AAA on body copy where it doesn't sacrifice density.
- **Both themes** must independently meet contrast targets — light theme's cobalt accent on warm-bone paper and dark theme's amber on near-black both verified.
- **Keyboard-first**: full keyboard navigation, visible focus rings using accent color, skip-to-content on the webapp shell.
- **Reduced motion**: honor `prefers-reduced-motion`. The dashboard's 2 s poll should not animate scalar updates when reduced motion is set.
- **Color-independent state**: HIT/MISS, pass/fail, success/danger states pair color with iconography or label text — never color alone.
- **Operator data must remain copyable**: never break selection or sneak hidden characters into IDs, hashes, payload blocks.

## Design Principles

1. **Theme is the audience selector — light is central.** Light = primary register (editorial-cobalt, plain language, the default load, what the brand identifies with publicly). Dark = alternate operator mode (DOOM/lava, dense, terminal-adjacent, raw power — opt-in for deep work). Same components, same Potter, same wordmark; palette + copy density + framing all swap together.
2. **The file tree IS the dashboard — the webapp augments it.** Never reinvent what the operator already reads in their editor. The webapp earns its place by showing live state and cross-cycle views the file tree can't, not by hiding what's already there.
3. **Both themes are first-class — but light is central.** Neither is a port. Both warrant equal craft, equal contrast verification, equal review. If you change one, change both.
4. **The Potter is a hint, not a costume.** Superman-tier force multiplier, not a friendly wizard. Mascot and wordmark carry the charm; copy stays plain (→ [`VOICE.md`](VOICE.md)). Sideways lasers, never downward sparkles.
5. **Whitelabel-safe by default.** Accent color, mark, and brand copy flow through tokens (`--color-accent`, `--brand-name`). Never hardcode "PromptPotter" into a styled element that resists rebrand. **What a distributor actually sets, and what it must never rename** — owned by [`docs/developer/whitelabel.md`](docs/developer/whitelabel.md); a new brand element here needs a declaration there, or it is unreachable.
6. **Accessible because the brand demands it.** Anti-nerdy / pro-accessibility means WCAG isn't a checklist — it's a feature we sell. Contrast, keyboard, motion respect, and color-independent state are non-negotiable.
7. **Substance over spectacle.** Animations and ornamentation must serve comprehension. The optimizer is the show; the UI gets out of the way.
8. **Marketing copy stays in `promptpotter-web`; visual tokens are shared.** Astro page structure, hero copy, CTAs, and the waitlist form live in the sibling repo. The brand palette, mark, logo set, and asset library are the same in both — change them in one place (this `BRAND.md` is the spec; tokens live in `webapp/app/styles/foundation/tokens.css`) and port to the other.

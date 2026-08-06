# BRAND.md — Visual identity

> Single source of truth for **design** across this repo, the `/ui` webapp, and the sibling marketing repo `promptpotter-web`. Copy register and language rules live in [`VOICE.md`](VOICE.md). Tokens live in `webapp/app/styles/foundation/tokens.css`.

PromptPotter is **LLM-driven program evolution** for prompts and pipeline params. CLI-first today; the file tree (per-cycle `dashboard.json`, `rounds/`, `log.md`) is the operator's primary surface, with a read-only webapp at `/ui` (Next.js static export at `webapp/out/`, source at `webapp/`) polling `dashboard.json` every 2 s. M12 promotes the webapp to a full control plane while keeping the file tree authoritative. Whitelabel distribution is a stated goal — every brand element must be themable.

The public **pre-release landing + waitlist site** is a *separate* Astro repo at `promptpotter-web` (sibling of this repo). Both repos share the **light-register elegant palette** and the logo/mark set. This document covers the CLI and the `/ui` webapp directly; marketing copy, CTAs, and Astro page structure live in the sibling repo and read the same tokens.

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
- **Mark — the Potter**: portrait + wordmark. Used judiciously. The Potter reads as **Superman-tier powerful**, not whimsical. When depicted with energy beams, lasers shoot **sideways** (left/right from his POV), never down. `PromptPotter` always renders as literal text in branded imagery. Naming conventions for the mark in copy → [`VOICE.md`](VOICE.md).
- **Motion**: Subtle and purposeful. Live-data transitions (status dot pulse, progress fills, polling refresh) are the only animations that earn their frame budget. No scroll-jacking, no parallax, no decorative motion.

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

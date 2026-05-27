# M13 — Onboarding Lockout

**Status:** shipped · 2026-05-27
**Sibling spec:** [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md) — separate, longer-arc workstream; both ship independently under the M13 tag.

Invite-gated front door for the live beta at `https://app.promptpotter.dev`. One slice of the v0.7.0 hosting arc: OIDC + allowlist + per-user quotas (`feat(m10): multi-user beta hosting`, `ADR-0002` Stage 1) + `deploy-linux/` (systemd + Cloudflare Tunnel) + this lockout.

## Surfaces

- **Marketing (`promptpotter-web/`)** — `Sign in →` chip in `Nav.astro`; `Already have a beta invite?` secondary CTA under the hero; centered `Terms · Privacy · Imprint` footer; three Astro stub pages (`src/pages/{terms,privacy,imprint}.astro`).
- **Webapp (`webapp/`)** — `lib/auth-context.tsx` probes `/api/v1/auth/me` on mount + focus, exposes `useAuth()` (`loading | authed | unauthed`). `components/shell/Topbar.tsx` right cluster gates on status: account icon + AccountModal when authed; `Log in` (yellow `.auth-chip-gold`) + `Sign up for free` (rust `.auth-chip-rust`) chips when unauthed, both opening the same tall ChatGPT-shape modal at `components/onboarding/WelcomeLockoutModal.tsx`. Modal layout: tagline → consent line with blue privacy link → invite-only narrative → `Continue with Google` → `OR` → email field + `Continue` (routes to operator LinkedIn until an email backend exists) → centered `Terms · Privacy · Imprint`. Five new CSS rules in `globals.css` (`.auth-chip{,-gold,-rust}`, `.auth-divider`, `.auth-link`, `.auth-legal-row`); everything else reuses existing `.account-*` + `.login-button` + `.chat-input` primitives.
- **No backend changes** — OIDC + allowlist were already wired.

## Owner / install identity

Per-template config in `webapp/lib/instance.ts` and `promptpotter-web/src/data/instance.ts` (name, LinkedIn, jurisdiction, marketing/app URLs). Forks edit those two files; no other source touches owner literals.

## Parallel fix

`webapp/components/whatif/FitnessPanel.tsx` seed effect now early-returns on `!cycleId` — fixes the React #185 max-update-depth crash that fired post-login when no campaign was active. Stale comment scrubbed from `DashboardPane.tsx`.

## Open

Real legal copy before public (non-invite) launch. Email sign-in is layout-only — wire if/when a backend lands.

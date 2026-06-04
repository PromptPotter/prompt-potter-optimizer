// Single source of brand identity for the embed / meta surfaces. Consumed by
// the Web App Manifest (`app/manifest.ts`), the schema.org JSON-LD in the page
// `<head>` (`app/layout.tsx`), and the Account → "About this unit" pane. A
// whitelabel host overrides any field via `NEXT_PUBLIC_*` at build time.
//
//   publisher — the distributing brand (the host; overridable).
//   provider  — who actually powers the unit (PromptPotter; FIXED — it is the
//               provenance fact, not a label the host gets to repaint).
//
// The live app VERSION is deliberately NOT here. It is server-owned
// (`APP_VERSION` in `config/settings.py`) and read at runtime from
// `/api/v1/health` — one source of truth, no build-time copy to drift.
//
// NOTE: Next inlines `process.env.NEXT_PUBLIC_*` only on *literal* member
// access, so each field reads its env var directly (no dynamic lookup helper).

export const BRAND = {
  name: process.env.NEXT_PUBLIC_BRAND_NAME || "PromptPotter Live Unit",
  shortName: process.env.NEXT_PUBLIC_BRAND_SHORT_NAME || "PromptPotter",
  description:
    process.env.NEXT_PUBLIC_BRAND_DESCRIPTION ||
    "LLM-driven program evolution for prompts and pipeline parameters.",
  url: process.env.NEXT_PUBLIC_BRAND_URL || "https://app.promptpotter.dev",
  applicationCategory: "DeveloperApplication",
  publisher: {
    name: process.env.NEXT_PUBLIC_PUBLISHER_NAME || "PromptPotter",
    url: process.env.NEXT_PUBLIC_PUBLISHER_URL || "https://promptpotter.dev",
  },
  provider: {
    name: "PromptPotter",
    url: "https://promptpotter.dev",
  },
  // The origin marketing site this unit links home to. OUR hosted instance
  // points back to promptpotter.com; a whitelabel distributor who resells /
  // hosts for profit sets NEXT_PUBLIC_MARKETING_URL="" to drop the login
  // "visit our website" card entirely — it must never funnel their paying
  // users upstream. `??` (not `||`) so an explicit empty string opts out.
  marketing: {
    url: process.env.NEXT_PUBLIC_MARKETING_URL ?? "https://promptpotter.com",
    title: process.env.NEXT_PUBLIC_MARKETING_TITLE || "PromptPotter",
    tagline:
      process.env.NEXT_PUBLIC_MARKETING_TAGLINE ||
      "LLM-driven program evolution — fix a broken LLM pipeline in half a day, then it just works.",
  },
  supportUrl: process.env.NEXT_PUBLIC_SUPPORT_URL || "https://promptpotter.dev/support",
  legalUrl: process.env.NEXT_PUBLIC_LEGAL_URL || "https://promptpotter.dev/legal",
  license: process.env.NEXT_PUBLIC_LICENSE || "https://promptpotter.dev/legal/license",
  // Mirrors the dark-theme body background in app/styles/foundation/themes.css + layout's
  // themeColor — the install/splash chrome a browser paints from the manifest.
  themeColor: "#0d0d0d",
  backgroundColor: "#0d0d0d",
  // Provenance trust level. `self-declared` until a signed, origin-bound
  // credential lands; the About pane must not show a "verified" affordance
  // while this says otherwise.
  verification: "self-declared" as "self-declared" | "verified",
} as const;

// schema.org SoftwareApplication — the provenance object. Emitted as JSON-LD
// in the page <head> (where crawlers/agents read it) AND shown verbatim in the
// About pane's "View provenance". One builder, so the two never diverge.
// softwareVersion is intentionally absent — version is live from /health.
export interface SoftwareApplicationLd {
  "@context": "https://schema.org";
  "@type": "SoftwareApplication";
  name: string;
  description: string;
  applicationCategory: string;
  url: string;
  provider: { "@type": "Organization"; name: string; url: string };
  publisher: { "@type": "Organization"; name: string; url: string };
}

export function softwareApplicationLd(): SoftwareApplicationLd {
  return {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: BRAND.name,
    description: BRAND.description,
    applicationCategory: BRAND.applicationCategory,
    url: BRAND.url,
    provider: {
      "@type": "Organization",
      name: BRAND.provider.name,
      url: BRAND.provider.url,
    },
    publisher: {
      "@type": "Organization",
      name: BRAND.publisher.name,
      url: BRAND.publisher.url,
    },
  };
}

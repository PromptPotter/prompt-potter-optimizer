// Single source of brand identity, each field `NEXT_PUBLIC_*`-overridable. Next inlines those
// only on LITERAL member access — never a dynamic lookup helper.

export const BRAND = {
  name: process.env.NEXT_PUBLIC_BRAND_NAME || "PromptPotter Live Unit",
  shortName: process.env.NEXT_PUBLIC_BRAND_SHORT_NAME || "PromptPotter",
  description:
    process.env.NEXT_PUBLIC_BRAND_DESCRIPTION ||
    "LLM-driven program evolution for prompts and pipeline parameters.",
  url: process.env.NEXT_PUBLIC_BRAND_URL || "https://app.promptpotter.com",
  applicationCategory: "DeveloperApplication",
  publisher: {
    name: process.env.NEXT_PUBLIC_PUBLISHER_NAME || "PromptPotter",
    url: process.env.NEXT_PUBLIC_PUBLISHER_URL || "https://promptpotter.com",
  },
  // Fixed: the provenance fact, never repainted by a whitelabel host.
  provider: {
    name: "PromptPotter",
    url: "https://promptpotter.com",
  },
  // `??`, not `||`: an explicit empty string drops the login "visit our website" card, so a
  // whitelabel host never funnels its users upstream.
  marketing: {
    url: process.env.NEXT_PUBLIC_MARKETING_URL ?? "https://promptpotter.com",
    title: process.env.NEXT_PUBLIC_MARKETING_TITLE || "PromptPotter",
    tagline:
      process.env.NEXT_PUBLIC_MARKETING_TAGLINE ||
      "Give PromptPotter the prompt you're already using; it returns critiqued, improved versions with rich evaluation metadata — a significant, measured gain in about five minutes.",
  },
  supportUrl:
    process.env.NEXT_PUBLIC_SUPPORT_URL ||
    "https://github.com/PromptPotter/prompt-potter-optimizer/issues",
  // Never derived from `marketing.url`: clearing that must not take the consent links with it.
  legal: {
    terms: process.env.NEXT_PUBLIC_TERMS_URL || "https://promptpotter.com/terms",
    privacy: process.env.NEXT_PUBLIC_PRIVACY_URL || "https://promptpotter.com/privacy",
    imprint: process.env.NEXT_PUBLIC_IMPRINT_URL || "https://promptpotter.com/imprint",
  },
  license:
    process.env.NEXT_PUBLIC_LICENSE ||
    "https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/LICENSE",
  // Mirrors the dark-theme body background in `foundation/themes.css` and layout's themeColor.
  themeColor: "#0d0d0d",
  backgroundColor: "#0d0d0d",
  // The About pane must never show a "verified" affordance while this says `self-declared`.
  verification: "self-declared" as "self-declared" | "verified",
} as const;

// One builder for the <head> JSON-LD and the About pane's "View provenance". No softwareVersion:
// the version is live from /health.
interface SoftwareApplicationLd {
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

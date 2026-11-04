// A deploy brands the build through `VITE_*` (`deploy-linux/brand-env.sh`); an unset one keeps the default here.
export function brandOf(env: Readonly<Record<string, string | undefined>>) {
  return {
    name: env.VITE_BRAND_NAME || "PromptPotter Live Unit",
    shortName: env.VITE_BRAND_SHORT_NAME || "PromptPotter",
    description:
      env.VITE_BRAND_DESCRIPTION ||
      "LLM-driven program evolution for prompts and pipeline parameters.",
    url: env.VITE_BRAND_URL || "https://app.promptpotter.com",
    applicationCategory: "DeveloperApplication",
    publisher: {
      name: env.VITE_PUBLISHER_NAME || "PromptPotter",
      url: env.VITE_PUBLISHER_URL || "https://promptpotter.com",
    },
    // Fixed: the provenance fact, never repainted by a whitelabel host.
    provider: {
      name: "PromptPotter",
      url: "https://promptpotter.com",
    },
    // `??`, not `||`: an explicit empty string drops the login "visit our website" card.
    marketing: {
      url: env.VITE_MARKETING_URL ?? "https://promptpotter.com",
      title: env.VITE_MARKETING_TITLE || "PromptPotter",
      tagline:
        env.VITE_MARKETING_TAGLINE ||
        "Give PromptPotter the prompt you're already using; it returns critiqued, improved versions with rich evaluation metadata.",
    },
    supportUrl:
      env.VITE_SUPPORT_URL || "https://github.com/PromptPotter/prompt-potter-optimizer/issues",
    // Never derived from `marketing.url`: clearing that must not take the consent links with it.
    legal: {
      terms: env.VITE_TERMS_URL || "https://promptpotter.com/terms",
      privacy: env.VITE_PRIVACY_URL || "https://promptpotter.com/privacy",
      imprint: env.VITE_IMPRINT_URL || "https://promptpotter.com/imprint",
    },
    license:
      env.VITE_LICENSE ||
      "https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/LICENSE",
    // Mirrors the dark ground in `foundation/tokens.css` and the page head's theme-color.
    themeColor: "#181818",
    backgroundColor: "#181818",
    verification: "self-declared" as "self-declared" | "verified",
  } as const;
}

export type Brand = ReturnType<typeof brandOf>;

// No softwareVersion: the version is live from /health.
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

export function softwareApplicationLd(brand: Brand): SoftwareApplicationLd {
  return {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: brand.name,
    description: brand.description,
    applicationCategory: brand.applicationCategory,
    url: brand.url,
    provider: {
      "@type": "Organization",
      name: brand.provider.name,
      url: brand.provider.url,
    },
    publisher: {
      "@type": "Organization",
      name: brand.publisher.name,
      url: brand.publisher.url,
    },
  };
}

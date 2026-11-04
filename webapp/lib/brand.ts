import { brandOf, type Brand } from "@/lib/brand-of";

// Literal member reads, never `brandOf(import.meta.env)`: Vite inlines each named `VITE_*`, and a whole-object read would inline them all.
export const BRAND: Brand = brandOf({
  VITE_BRAND_NAME: import.meta.env.VITE_BRAND_NAME,
  VITE_BRAND_SHORT_NAME: import.meta.env.VITE_BRAND_SHORT_NAME,
  VITE_BRAND_DESCRIPTION: import.meta.env.VITE_BRAND_DESCRIPTION,
  VITE_BRAND_URL: import.meta.env.VITE_BRAND_URL,
  VITE_PUBLISHER_NAME: import.meta.env.VITE_PUBLISHER_NAME,
  VITE_PUBLISHER_URL: import.meta.env.VITE_PUBLISHER_URL,
  VITE_MARKETING_URL: import.meta.env.VITE_MARKETING_URL,
  VITE_MARKETING_TITLE: import.meta.env.VITE_MARKETING_TITLE,
  VITE_MARKETING_TAGLINE: import.meta.env.VITE_MARKETING_TAGLINE,
  VITE_SUPPORT_URL: import.meta.env.VITE_SUPPORT_URL,
  VITE_TERMS_URL: import.meta.env.VITE_TERMS_URL,
  VITE_PRIVACY_URL: import.meta.env.VITE_PRIVACY_URL,
  VITE_IMPRINT_URL: import.meta.env.VITE_IMPRINT_URL,
  VITE_LICENSE: import.meta.env.VITE_LICENSE,
});

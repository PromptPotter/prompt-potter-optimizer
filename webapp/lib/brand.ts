import type { Brand } from "@/lib/brand-of";

// `vite.config.ts` resolves `brandOf` once from the deploy's `VITE_*` and defines the result here, so the page head,
// the manifest and this constant can never name a different brand.
declare const __BRAND__: Brand;
export const BRAND = __BRAND__;

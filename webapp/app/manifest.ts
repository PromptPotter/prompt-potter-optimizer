import type { MetadataRoute } from "next";
import { BRAND } from "@/lib/brand";

// Required for `output: export` — emit the manifest as a static file at
// build time rather than a request-time route.
export const dynamic = "force-static";

// The Web App Manifest — the browser-consumed app-identity surface (install
// name, icons, splash colors), generated from the single brand source. Served
// at /ui/manifest.webmanifest; Next auto-links it (basePath-aware). Icon paths
// carry the /ui basePath explicitly because manifest icon `src` is resolved
// against the authority root, not the manifest URL.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: BRAND.name,
    short_name: BRAND.shortName,
    description: BRAND.description,
    start_url: "/ui/",
    scope: "/ui/",
    display: "standalone",
    background_color: BRAND.backgroundColor,
    theme_color: BRAND.themeColor,
    icons: [
      { src: "/ui/brand/potter-mark.svg", sizes: "any", type: "image/svg+xml" },
    ],
  };
}

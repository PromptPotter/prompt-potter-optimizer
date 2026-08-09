import type { MetadataRoute } from "next";
import { BRAND } from "@/lib/brand";

// Required for `output: export` — emit the manifest as a static file at
// build time rather than a request-time route.
export const dynamic = "force-static";

// The Web App Manifest — the browser-consumed app-identity surface (install
// name, icons, splash colors), generated from the single brand source. Served
// at /manifest.webmanifest; Next auto-links it. The app owns the domain root,
// so start_url/scope/icon paths are root-relative.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: BRAND.name,
    short_name: BRAND.shortName,
    description: BRAND.description,
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: BRAND.backgroundColor,
    theme_color: BRAND.themeColor,
    icons: [
      // Opaque, on brand paper — an installed-app icon is composited onto a
      // surface we do not control, so a transparent mark would vanish on a
      // dark launcher.
      { src: "/brand/app-icon-pot-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}

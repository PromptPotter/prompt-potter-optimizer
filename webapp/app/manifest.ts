import type { MetadataRoute } from "next";
import { BRAND } from "@/lib/brand";

// Required for `output: export`.
export const dynamic = "force-static";

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
      // Opaque: a transparent mark would vanish on a dark launcher.
      { src: "/brand/app-icon-pot-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}

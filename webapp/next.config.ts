import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  // Served from FastAPI's /ui StaticFiles mount in production. basePath
  // makes Next emit /ui-prefixed asset URLs in the exported HTML.
  basePath: "/ui",
  // Trailing slashes match StaticFiles(html=True) behavior — /ui/files/
  // resolves to <dir>/files/index.html.
  trailingSlash: true,
  images: { unoptimized: true },
  // Pin the workspace root so a stray ~/package-lock.json doesn't confuse
  // Turbopack's workspace inference.
  turbopack: { root: path.resolve(__dirname) },
  async rewrites() {
    // Dev-mode only — `next build` with `output: "export"` strips rewrites.
    // In production we serve under /ui on the same FastAPI origin as /api,
    // so no proxy is needed.
    return [
      { source: "/api/:path*", destination: "http://127.0.0.1:8001/api/:path*" },
    ];
  },
};

export default nextConfig;

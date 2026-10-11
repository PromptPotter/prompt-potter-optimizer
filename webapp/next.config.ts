import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  typescript: { ignoreBuildErrors: true },
  reactCompiler: true,
  productionBrowserSourceMaps: true,
  // Matches FastAPI's StaticFiles(html=True): /files/ resolves to files/index.html.
  trailingSlash: true,
  images: { unoptimized: true },
  // Pinned so a stray ~/package-lock.json cannot move Turbopack's inferred workspace root.
  turbopack: { root: path.resolve(__dirname) },
  async rewrites() {
    // Dev-mode only: `output: "export"` strips rewrites, and production shares the API's origin.
    return [
      { source: "/api/:path*", destination: "http://127.0.0.1:8001/api/:path*" },
    ];
  },
};

export default nextConfig;

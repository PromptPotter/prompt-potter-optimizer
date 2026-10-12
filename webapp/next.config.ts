import fs from "node:fs";
import path from "node:path";
import type { NextConfig } from "next";

// Written by `npm run build` before Next starts and exported with `public/`, so the server reads
// the same id this bakes into the client. Absent under `next dev`, where nothing is compared.
const buildStamp = path.resolve(__dirname, "public", "build-id.txt");

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_WEBAPP_BUILD: fs.existsSync(buildStamp) ? fs.readFileSync(buildStamp, "utf8") : "",
  },
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

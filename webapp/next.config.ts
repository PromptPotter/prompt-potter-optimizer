import path from "node:path";
import type { NextConfig } from "next";

// DEPLOY_BUILD=1 (`npm run build:deploy`, the gate) alone pays for the React Compiler and source
// maps; a bare `npm run build` is the fast preview loop.
const deployBuild = process.env.DEPLOY_BUILD === "1";
// `scripts/gate.py` exports its per-check CPU slice; unset, Turbopack claims every core.
const gateJobs = Number(process.env.GATE_JOBS) || 0;

const nextConfig: NextConfig = {
  output: "export",
  // Type-checking is its own gate (`npx tsc --noEmit`); `next build`'s pass does not hard-fail.
  typescript: { ignoreBuildErrors: true },
  // Deploy-only, yet it changes runtime behaviour — so only the shipped build exercises it.
  reactCompiler: deployBuild,
  productionBrowserSourceMaps: deployBuild,
  // Matches FastAPI's StaticFiles(html=True): /files/ resolves to files/index.html.
  trailingSlash: true,
  images: { unoptimized: true },
  // Pinned so a stray ~/package-lock.json cannot move Turbopack's inferred workspace root.
  turbopack: { root: path.resolve(__dirname) },
  ...(gateJobs ? { experimental: { cpus: gateJobs } } : {}),
  async rewrites() {
    // Dev-mode only: `output: "export"` strips rewrites, and production shares the API's origin.
    return [
      { source: "/api/:path*", destination: "http://127.0.0.1:8001/api/:path*" },
    ];
  },
};

export default nextConfig;

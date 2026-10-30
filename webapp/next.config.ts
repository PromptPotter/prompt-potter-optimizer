import fs from "node:fs";
import path from "node:path";
import type { NextConfig } from "next";

// Written by `npm run build` before Next starts and exported with `public/`, so the server reads
// the same id this bakes into the client. Absent under `next dev`, where nothing is compared.
const buildStamp = path.resolve(__dirname, "public", "build-id.txt");

// Turbopack resolves nothing outside its root. A side checkout (the pre-push gate's, a worktree)
// links `node_modules` to the main tree's, so the root is the nearest folder holding both.
function workspaceRoot(): string {
  const modules = path.join(__dirname, "node_modules");
  const linked = fs.existsSync(modules) ? fs.realpathSync(modules) : modules;
  let root = __dirname;
  while (path.relative(root, linked).startsWith("..")) root = path.dirname(root);
  return root;
}

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
  turbopack: { root: workspaceRoot() },
  // The gate sizes the static-generation workers from free memory (`scripts/gate.py::RamBudget`).
  ...(process.env.NEXT_BUILD_CPUS && { experimental: { cpus: Number(process.env.NEXT_BUILD_CPUS) } }),
  async rewrites() {
    // Dev-mode only: `output: "export"` strips rewrites, and production shares the API's origin.
    return [
      { source: "/api/:path*", destination: "http://127.0.0.1:8001/api/:path*" },
    ];
  },
};

export default nextConfig;

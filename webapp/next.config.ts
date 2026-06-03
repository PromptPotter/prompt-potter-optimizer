import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  // Auto-memoize components + hooks. Lets us drop the manual React.memo /
  // useMemo / useCallback wrappers in a follow-up audit; the FitnessChart /
  // TrendChart / TopStrip / LineageTree render-cost guards still hold.
  reactCompiler: true,
  // Emit .map files alongside minified .js in the static export so a live
  // DevTools session on a deployed dashboard resolves React errors to
  // component + line. Without this, production stacks read like
  // "Minified React error #185" with no actionable frame.
  productionBrowserSourceMaps: true,
  // Served at the domain root by FastAPI's StaticFiles mount in production —
  // the app owns `/`, the API is the carved-out `/api/v1` namespace. No
  // basePath: asset URLs emit at /_next/... (the claude.ai serving shape).
  // Trailing slashes match StaticFiles(html=True) behavior — /files/
  // resolves to <dir>/files/index.html.
  trailingSlash: true,
  images: { unoptimized: true },
  // Pin the workspace root so a stray ~/package-lock.json doesn't confuse
  // Turbopack's workspace inference.
  turbopack: { root: path.resolve(__dirname) },
  async rewrites() {
    // Dev-mode only — `next build` with `output: "export"` strips rewrites.
    // In production we serve at the root on the same FastAPI origin as /api,
    // so no proxy is needed.
    return [
      { source: "/api/:path*", destination: "http://127.0.0.1:8001/api/:path*" },
    ];
  },
};

export default nextConfig;

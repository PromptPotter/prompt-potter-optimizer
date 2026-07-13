import path from "node:path";
import type { NextConfig } from "next";

// `next build` has two audiences: the operator's local rebuild→reload preview
// loop (bare `npm run build`) and the shipped artifact (CI + the deploy box,
// `npm run build:deploy`, which sets DEPLOY_BUILD=1). Only the deploy artifact
// needs the React Compiler pass + full-bundle source maps; folding them into
// every build is what turned the ~5s preview build into ~20s.
const deployBuild = process.env.DEPLOY_BUILD === "1";

const nextConfig: NextConfig = {
  output: "export",
  // Type-checking is owned by a dedicated CI gate (`npx tsc --noEmit`; see
  // webapp/CLAUDE.md § Testing posture). `next build`'s own pass only duplicates
  // it — and doesn't even hard-fail — so re-running it in the build is redundant
  // work at the wrong layer. Authority lives upstream; the build just compiles.
  // (This is the ~8s "Finished TypeScript".) ESLint isn't configured here at all
  // — Next 16 dropped build-time linting; `npm run lint` is its sole gate.
  typescript: { ignoreBuildErrors: true },
  // Auto-memoize components + hooks. Lets us drop the manual React.memo /
  // useMemo / useCallback wrappers in a follow-up audit; the FitnessChart /
  // TrendChart / TopStrip / candidates-card render-cost guards still hold. Changes
  // runtime behaviour, so it's validated on the shipped artifact only.
  reactCompiler: deployBuild,
  // Emit .map files alongside minified .js in the static export so a live
  // DevTools session on a deployed dashboard resolves React errors to
  // component + line. Without this, production stacks read like a bare
  // "Minified React error" code with no actionable frame. Deploy-only.
  productionBrowserSourceMaps: deployBuild,
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

import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type HtmlTagDescriptor, type Plugin } from "vite";
import { brandOf, softwareApplicationLd, type Brand } from "./lib/brand-of.ts";
import { STORAGE_KEYS } from "./lib/storage-keys.ts";

const WEBAPP = import.meta.dirname;
const APP = path.join(WEBAPP, "app");
// `vitest.config.ts` resolves through it too.
export const RESOLVE = { alias: { "@": WEBAPP } };

const CARD_TITLE = "PromptPotter — automatic prompt optimizer for better AI answers";
const CARD_DESC =
  "Give PromptPotter the prompt you used on your AI provider. It critiques and " +
  "improves it on its own, then shows a significant, measured gain — in about five minutes.";

// Stamps `data-theme` before the first paint: a classic script outside the bundle, so it runs
// before any module, and a file, so the page CSP needs no inline script. Its key is the table's.
const THEME_INIT = "theme-init.js";
const THEME_INIT_SOURCE = `(function () {
  var s = null;
  try {
    s = localStorage.getItem(${JSON.stringify(STORAGE_KEYS.theme)});
  } catch (_) {}
  if ((s || "light") === "light") document.documentElement.setAttribute("data-theme", "light");
})();
`;

function head(brand: Brand): HtmlTagDescriptor[] {
  const meta = (attrs: Record<string, string>): HtmlTagDescriptor => ({ tag: "meta", attrs });
  return [
    meta({ name: "viewport", content: "width=device-width, initial-scale=1, viewport-fit=cover" }),
    // `data-theme` cannot drive this; the OS scheme query is the closest signal.
    meta({ name: "theme-color", content: "#181818", media: "(prefers-color-scheme: dark)" }),
    meta({ name: "theme-color", content: "#F5F1EA", media: "(prefers-color-scheme: light)" }),
    { tag: "title", children: "optimize, potter, learn" },
    meta({ name: "description", content: brand.description }),
    meta({ name: "application-name", content: brand.shortName }),
    { tag: "link", attrs: { rel: "author", href: brand.provider.url } },
    meta({ name: "author", content: brand.provider.name }),
    meta({ name: "creator", content: brand.provider.name }),
    meta({ name: "publisher", content: brand.publisher.name }),
    { tag: "link", attrs: { rel: "manifest", href: "/manifest.webmanifest" } },
    meta({ property: "og:type", content: "website" }),
    meta({ property: "og:url", content: new URL(brand.url).href }),
    meta({ property: "og:title", content: CARD_TITLE }),
    meta({ property: "og:description", content: CARD_DESC }),
    meta({ name: "twitter:card", content: "summary" }),
    meta({ name: "twitter:title", content: CARD_TITLE }),
    meta({ name: "twitter:description", content: CARD_DESC }),
    ...(["light", "dark"] as const).map((scheme) => ({
      tag: "link",
      attrs: {
        rel: "icon",
        type: "image/png",
        media: `(prefers-color-scheme: ${scheme})`,
        href: `/brand/tab-icon-pot-32${scheme === "dark" ? "-dark" : ""}.png`,
      },
    })),
    { tag: "script", attrs: { src: `/${THEME_INIT}` } },
    {
      tag: "script",
      attrs: { type: "application/ld+json" },
      children: JSON.stringify(softwareApplicationLd(brand)).replaceAll("<", "\\u003c"),
    },
  ];
}

// The globals a build compiles in; `vitest.config.ts` defines through it too, on the defaults.
export function defines(brand: Brand, build: string | null): Record<string, string> {
  return {
    // A bare global, not `import.meta.env.*`: Vitest moves those into `process.env`, which stringifies an object.
    __BRAND__: JSON.stringify(brand),
    ...(build ? { "import.meta.env.VITE_WEBAPP_BUILD": JSON.stringify(build) } : {}),
  };
}

// What every page shares and the server reads beside them: the head, the theme script, the manifest, the build id.
function shell(brand: Brand, build: string | null): Plugin {
  return {
    name: "promptpotter-shell",
    // After the page's own `<meta charset>`, which must open the head.
    transformIndexHtml: () => head(brand).map((tag) => ({ ...tag, injectTo: "head" as const })),
    configureServer(server) {
      server.middlewares.use(`/${THEME_INIT}`, (_req, res) => {
        res.setHeader("Content-Type", "text/javascript");
        res.end(THEME_INIT_SOURCE);
      });
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: THEME_INIT, source: THEME_INIT_SOURCE });
      const manifest = {
        name: brand.name,
        short_name: brand.shortName,
        description: brand.description,
        start_url: "/",
        scope: "/",
        display: "standalone",
        background_color: brand.backgroundColor,
        theme_color: brand.themeColor,
        // Opaque: a transparent mark would vanish on a dark launcher.
        icons: [{ src: "/brand/app-icon-pot-512.png", sizes: "512x512", type: "image/png" }],
      };
      this.emitFile({ type: "asset", fileName: "manifest.webmanifest", source: JSON.stringify(manifest) });
      if (build) this.emitFile({ type: "asset", fileName: "build-id.txt", source: build });
    },
  };
}

export default defineConfig(({ command, mode }) => {
  // Only a build is compared with what the server serves; the dev server stamps none.
  const build = command === "build" ? Date.now().toString(36) : null;
  const brand = brandOf(loadEnv(mode, WEBAPP, "VITE_"));
  return {
    root: APP,
    publicDir: path.join(WEBAPP, "public"),
    envDir: WEBAPP,
    resolve: RESOLVE,
    define: defines(brand, build),
    plugins: [react(), shell(brand, build)],
    build: {
      outDir: path.join(WEBAPP, "out"),
      emptyOutDir: true,
      sourcemap: true,
      // Every page FastAPI's `StaticFiles(html=True)` serves: `/`, `/login/`, `/stage/`, and its miss page.
      rolldownOptions: {
        input: [
          path.join(APP, "index.html"),
          path.join(APP, "login", "index.html"),
          path.join(APP, "stage", "index.html"),
          path.join(APP, "404.html"),
        ],
      },
    },
    // `npm run dev` on :3000; production shares the API's origin.
    server: { port: 3000, proxy: { "/api": "http://127.0.0.1:8001" } },
  };
});

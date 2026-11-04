import path from "node:path";
import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type HtmlTagDescriptor, type Plugin } from "vite";
import { brandOf, softwareApplicationLd, type Brand } from "./lib/brand-of";

const WEBAPP = import.meta.dirname;
const APP = path.join(WEBAPP, "app");

const CARD_TITLE = "PromptPotter — automatic prompt optimizer for better AI answers";
const CARD_DESC =
  "Give PromptPotter the prompt you used on your AI provider. It critiques and " +
  "improves it on its own, then shows a significant, measured gain — in about five minutes.";

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
    // Stamps `data-theme` before the first paint; a file, so the page CSP needs no inline script.
    { tag: "script", attrs: { src: "/theme-init.js" } },
    {
      tag: "script",
      attrs: { type: "application/ld+json" },
      children: JSON.stringify(softwareApplicationLd(brand)).replaceAll("<", "\\u003c"),
    },
  ];
}

// What every page shares and the server reads beside them: the head, the manifest, the build id.
function shell(brand: Brand, build: string | null): Plugin {
  return {
    name: "promptpotter-shell",
    // After the page's own `<meta charset>`, which must open the head.
    transformIndexHtml: () => head(brand).map((tag) => ({ ...tag, injectTo: "head" as const })),
    generateBundle() {
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
    resolve: { alias: { "@": WEBAPP } },
    define: {
      // A bare global, not `import.meta.env.*`: Vitest moves those into `process.env`, which stringifies an object.
      __BRAND__: JSON.stringify(brand),
      ...(build ? { "import.meta.env.VITE_WEBAPP_BUILD": JSON.stringify(build) } : {}),
    },
    plugins: [
      react(),
      babel({ presets: [reactCompilerPreset()] }),
      shell(brand, build),
    ],
    build: {
      outDir: path.join(WEBAPP, "out"),
      emptyOutDir: true,
      sourcemap: true,
      // Every page FastAPI's `StaticFiles(html=True)` serves: `/`, `/login/`, and its miss page.
      rolldownOptions: {
        input: [
          path.join(APP, "index.html"),
          path.join(APP, "login", "index.html"),
          path.join(APP, "404.html"),
        ],
      },
    },
    // `npm run dev` on :3000; production shares the API's origin.
    server: { port: 3000, proxy: { "/api": "http://127.0.0.1:8001" } },
  };
});

import type { Metadata, Viewport } from "next";
import "./styles/index.css";
import { AuthProvider } from "@/lib/auth-context";
import { BRAND, softwareApplicationLd } from "@/lib/brand";

// Link-unfurl (share-card) copy — what WhatsApp/Slack/iMessage/X show when the
// URL is pasted. Mirrors promptpotter-web's split: a descriptive card title +
// punchy, jargon-free blurb (distinct from the terse browser-tab title above).
const CARD_TITLE = "PromptPotter — automatic prompt optimizer for better AI answers";
const CARD_DESC =
  "Give PromptPotter the prompt you used on your AI provider. It critiques and " +
  "improves it on its own, then shows a significant, measured gain — in about five minutes.";

export const metadata: Metadata = {
  metadataBase: new URL(BRAND.url),
  title: "optimize, potter, learn",
  // Emoji favicon as an inline SVG data-URI — no image asset to ship. Single
  // quotes inside so the generated href="" stays valid.
  icons: {
    icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏺</text></svg>",
  },
  description: BRAND.description,
  applicationName: BRAND.shortName,
  // publisher = the distributing brand; provider authored the software.
  publisher: BRAND.publisher.name,
  authors: [{ name: BRAND.provider.name, url: BRAND.provider.url }],
  creator: BRAND.provider.name,
  // No og:image — link unfurls show the title + blurb only (no thumbnail). A
  // social card image must be a real fetched raster; we're not committing a
  // brand asset pre-launch, so the card stays text-only for now.
  openGraph: {
    type: "website",
    url: BRAND.url,
    title: CARD_TITLE,
    description: CARD_DESC,
  },
  twitter: {
    card: "summary",
    title: CARD_TITLE,
    description: CARD_DESC,
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  // iOS Safari tints the address bar / status bar to themeColor. The
  // operator-toggleable theme attribute (`data-theme`) can't drive this
  // — the OS-level scheme query is the closest signal. Light/dark map
  // to the body background each theme uses.
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0d0d0d" },
    { media: "(prefers-color-scheme: light)", color: "#F5F1EA" },
  ],
};

// Inline pre-paint script: applies stored theme before first paint to avoid a
// flash of the wrong palette. Mirrors the IIFE in the vanilla file.
const themeInit = `(function(){var s=null;try{s=localStorage.getItem('promptpotter.theme');}catch(_){}var t=s||'light';if(t==='light')document.documentElement.setAttribute('data-theme','light');})();`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
        {/* schema.org provenance — who publishes vs. who powers this unit.
            The crawler/agent-readable surface; the About pane shows the same
            object via softwareApplicationLd(). */}
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareApplicationLd()) }}
        />
      </head>
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}

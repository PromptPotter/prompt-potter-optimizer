import type { Metadata, Viewport } from "next";
import "./styles/index.css";
import { AuthProvider } from "@/lib/auth-context";
import { SurfaceFavicon } from "@/components/brand/SurfaceFavicon";
import { BRAND, softwareApplicationLd } from "@/lib/brand";

// Link-unfurl copy, mirroring promptpotter-web's split: a descriptive card title, not the tab title.
const CARD_TITLE = "PromptPotter — automatic prompt optimizer for better AI answers";
const CARD_DESC =
  "Give PromptPotter the prompt you used on your AI provider. It critiques and " +
  "improves it on its own, then shows a significant, measured gain — in about five minutes.";

export const metadata: Metadata = {
  metadataBase: new URL(BRAND.url),
  title: "optimize, potter, learn",
  description: BRAND.description,
  applicationName: BRAND.shortName,
  // publisher = the distributing brand; provider authored the software.
  publisher: BRAND.publisher.name,
  authors: [{ name: BRAND.provider.name, url: BRAND.provider.url }],
  creator: BRAND.provider.name,
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
  // `data-theme` cannot drive this; the OS scheme query is the closest signal.
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0d0d0d" },
    { media: "(prefers-color-scheme: light)", color: "#F5F1EA" },
  ],
};

// Pre-paint, so the stored theme lands before the first frame.
const themeInit = `(function(){var s=null;try{s=localStorage.getItem('promptpotter.theme');}catch(_){}var t=s||'light';if(t==='light')document.documentElement.setAttribute('data-theme','light');})();`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        {/* Not `metadata.icons`: that is re-emitted at hydration, after `SurfaceFavicon`
            repaints, and takes the tab back. */}
        <link rel="icon" type="image/png" media="(prefers-color-scheme: light)" href="/brand/tab-icon-pot-32.png" />
        <link rel="icon" type="image/png" media="(prefers-color-scheme: dark)" href="/brand/tab-icon-pot-32-dark.png" />
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />

        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareApplicationLd()) }}
        />
      </head>
      <body>
        <SurfaceFavicon />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}

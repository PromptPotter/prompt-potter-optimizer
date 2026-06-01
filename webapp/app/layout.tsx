import type { Metadata, Viewport } from "next";
import "./styles/index.css";
import { AuthProvider } from "@/lib/auth-context";
import { BRAND, softwareApplicationLd } from "@/lib/brand";

export const metadata: Metadata = {
  metadataBase: new URL(BRAND.url),
  title: `${BRAND.shortName} — Live Unit`,
  description: BRAND.description,
  applicationName: BRAND.shortName,
  // publisher = the distributing brand; provider authored the software.
  publisher: BRAND.publisher.name,
  authors: [{ name: BRAND.provider.name, url: BRAND.provider.url }],
  creator: BRAND.provider.name,
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
const themeBootstrap = `(function(){var s=null;try{s=localStorage.getItem('promptpotter.theme');}catch(_){}var t=s||'light';if(t==='light')document.documentElement.setAttribute('data-theme','light');})();`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
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

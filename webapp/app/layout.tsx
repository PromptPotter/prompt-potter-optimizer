import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth-context";

export const metadata: Metadata = {
  title: "PromptPotter — Live Unit",
  description: "PromptPotter operator dashboard",
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
    { media: "(prefers-color-scheme: light)", color: "#b6c5d4" },
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
      </head>
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}

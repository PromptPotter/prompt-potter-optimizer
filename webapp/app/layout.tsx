import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PromptPotter — Live Unit",
  description: "PromptPotter operator dashboard",
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
      <body>{children}</body>
    </html>
  );
}

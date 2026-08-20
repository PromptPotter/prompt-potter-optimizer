"use client";
// Which install you are looking at, painted into the browser tab.
//
// The local preview and the deployed unit are the SAME static export served
// from two places, so the answer cannot be baked at build time — a deploy build
// previewed on localhost would wear the deployed colour and lie. It is read off
// the hostname on mount instead: loopback is the operator's own machine, and
// everything else is the unit (the box answers on a LAN address as well as on
// app.promptpotter.com; both are the same install).
//
// The marketing site keeps the untinted mark, so the three tabs a session ends
// up with — local, unit, promptpotter.com — are three different icons.

import { useEffect } from "react";

// The two BRAND.md accents, held as fixed surface colours. Deliberately NOT
// read from `--color-accent`: that follows the operator's theme, which would
// repaint the icon on a theme toggle and say nothing about which install it is.
const SURFACES = {
  local: { ground: "#f59e0b", mark: "/brand/tab-icon-pot-32.png" },
  unit: { ground: "#090C9B", mark: "/brand/tab-icon-pot-32-dark.png" },
} as const;

const SIZE = 32;
const CORNER = 7;
// The mark's ink box is 27 of the cut's 32px, centred; this leaves ~4px of
// ground around it at every edge.
const MARK_SCALE = 0.88;

function surfaceOf(hostname: string): keyof typeof SURFACES {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]"
    ? "local"
    : "unit";
}

export function SurfaceFavicon() {
  useEffect(() => {
    const { ground, mark } = SURFACES[surfaceOf(window.location.hostname)];
    const canvas = document.createElement("canvas");
    canvas.width = SIZE;
    canvas.height = SIZE;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const img = new Image();
    img.onload = () => {
      ctx.fillStyle = ground;
      ctx.beginPath();
      ctx.roundRect(0, 0, SIZE, SIZE, CORNER);
      ctx.fill();
      const drawn = SIZE * MARK_SCALE;
      ctx.drawImage(img, (SIZE - drawn) / 2, (SIZE - drawn) / 2, drawn, drawn);

      // Both declared cuts get the same href: the ground is opaque, so the
      // light/dark chrome split the two cuts exist for no longer applies.
      const href = canvas.toDataURL("image/png");
      document
        .querySelectorAll<HTMLLinkElement>('link[rel="icon"]')
        .forEach((link) => {
          link.href = href;
        });
    };
    img.src = mark;
  }, []);

  return null;
}

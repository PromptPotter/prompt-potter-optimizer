"use client";
// Which install you are looking at, painted into the browser tab.
//
// The local preview and the deployed unit are the SAME static export served
// from two places, so the answer cannot be baked at build time — a deploy build
// previewed on localhost would wear the unit's colours and lie. It is read off
// the hostname on mount instead: loopback is the operator's own machine, and
// everything else is the unit (the box answers on a LAN address as well as on
// app.promptpotter.com; both are the same install).
//
// Local is a gold disc, the unit a near-black rounded square, and the mark
// flips ink/gold to sit on each. Shape carries as much of the difference as
// colour, so the two stay apart at 16px and in a colour-blind tab strip. The
// marketing site keeps the untinted mark, which makes it the third icon.

import { useEffect } from "react";

const GOLD = "#f59e0b";
const INK = "#0a0a0a";

const SURFACES = {
  local: { ground: GOLD, mark: INK, shape: "disc" },
  unit: { ground: INK, mark: GOLD, shape: "square" },
} as const;

// Painted at 32px, the size the two static cuts are authored at.
const SIZE = 32;
// The mark is drawn from the 128px alpha master (the one `PotterMark` masks
// with), not from a tab cut: it is recoloured per surface, and only the master
// downscales cleanly. Its ink box within that 128px square:
const MARK_SRC = "/brand/mark-pot.png";
const INK_BOX = { x: 28, y: 5, w: 72, h: 118, of: 128 };
// Ink height as a fraction of the tile. ABOVE the static cuts' 0.84 on purpose
// — the mark breaks out of the shape behind it rather than being inset into
// it, and a tab icon this small can never afford to lose height to a ground.
const INK_HEIGHT = 0.92;
const DISC_RADIUS = 0.4 * SIZE;
const SQUARE_INSET = 0.5;
const SQUARE_CORNER = 0.22 * SIZE;

export function SurfaceFavicon() {
  useEffect(() => {
    const host = window.location.hostname;
    const isLocal = host === "localhost" || host === "127.0.0.1" || host === "[::1]";
    const { ground, mark, shape } = SURFACES[isLocal ? "local" : "unit"];

    const canvas = document.createElement("canvas");
    canvas.width = SIZE;
    canvas.height = SIZE;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const img = new Image();
    img.onload = () => {
      ctx.fillStyle = ground;
      ctx.beginPath();
      if (shape === "disc") {
        ctx.arc(SIZE / 2, SIZE / 2, DISC_RADIUS, 0, Math.PI * 2);
      } else {
        ctx.roundRect(
          SQUARE_INSET,
          SQUARE_INSET,
          SIZE - 2 * SQUARE_INSET,
          SIZE - 2 * SQUARE_INSET,
          SQUARE_CORNER,
        );
      }
      ctx.fill();

      // The master carries the silhouette in its alpha channel, so the mark is
      // recoloured the way the CSS mask does it: draw it, then flood the pixels
      // it covered. Off-screen, because `source-in` would eat the ground.
      const scale = (INK_HEIGHT * SIZE) / INK_BOX.h;
      const side = INK_BOX.of * scale;
      const tinted = document.createElement("canvas");
      tinted.width = SIZE;
      tinted.height = SIZE;
      const tctx = tinted.getContext("2d");
      if (!tctx) return;
      tctx.imageSmoothingQuality = "high";
      tctx.drawImage(
        img,
        SIZE / 2 - (INK_BOX.x + INK_BOX.w / 2) * scale,
        SIZE / 2 - (INK_BOX.y + INK_BOX.h / 2) * scale,
        side,
        side,
      );
      tctx.globalCompositeOperation = "source-in";
      tctx.fillStyle = mark;
      tctx.fillRect(0, 0, SIZE, SIZE);
      ctx.drawImage(tinted, 0, 0);

      // Both declared cuts get the same href: the ground is opaque, so the
      // light/dark chrome split the two cuts exist for no longer applies.
      const href = canvas.toDataURL("image/png");
      document
        .querySelectorAll<HTMLLinkElement>('link[rel="icon"]')
        .forEach((link) => {
          link.href = href;
        });
    };
    img.src = MARK_SRC;
  }, []);

  return null;
}

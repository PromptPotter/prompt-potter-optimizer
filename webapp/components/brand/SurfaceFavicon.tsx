"use client";
// Which install you are looking at, painted into the browser tab. Read off the hostname at mount,
// never baked at build: local and deployed serve the SAME static export.

import { useEffect } from "react";

const GOLD = "#f59e0b";
const INK = "#0a0a0a";

const SURFACES = {
  local: { ground: GOLD, mark: INK, shape: "disc" },
  unit: { ground: INK, mark: GOLD, shape: "square" },
} as const;

const SIZE = 32;
// Drawn from the 128px alpha master, not a tab cut: only the master recolours and downscales cleanly.
const MARK_SRC = "/brand/mark-pot.png";
const INK_BOX = { x: 28, y: 5, w: 72, h: 118, of: 128 };
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

      // Off-screen, because `source-in` would eat the ground.
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

      // Both declared cuts get the same href: the ground is opaque, so the light/dark split is moot.
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

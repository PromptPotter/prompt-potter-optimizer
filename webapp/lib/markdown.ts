import { Marked } from "marked";

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Artifacts quote tenant rows, so raw HTML is escaped; a private `Marked`, since `marked.use` mutates the global.
const safeMarked = new Marked({
  renderer: {
    html(token) {
      return escapeHtml(token.raw);
    },
  },
});

export function renderMarkdownSafe(source: string): string {
  const html = safeMarked.parse(source, { async: false });
  return typeof html === "string" ? html : source;
}

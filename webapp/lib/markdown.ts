import { Marked } from "marked";

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Browsers drop C0 controls and spaces inside a scheme, so the test reads the URL as they do; `&` could
// spell a colon as an entity, so a URL without an allowed scheme passes only if it has neither.
function isSafeHref(href: string): boolean {
  const seen = href.replace(/[\u0000- ]/g, "");
  return /^(https?|mailto):/i.test(seen) || !/[:&\\]/.test(seen);
}

// Artifacts quote tenant rows, so raw HTML is escaped, an unsafe link renders as its text, and an
// image as its alt text: no generated artifact embeds one, and any src is a request the tenant aims.
// A private `Marked`, since `marked.use` mutates the global.
const safeMarked = new Marked({
  renderer: {
    html(token) {
      return escapeHtml(token.raw);
    },
    link(token) {
      return isSafeHref(token.href) ? false : this.parser.parseInline(token.tokens);
    },
    image(token) {
      return escapeHtml(token.text);
    },
  },
});

export function renderMarkdownSafe(source: string): string {
  const html = safeMarked.parse(source, { async: false });
  return typeof html === "string" ? html : source;
}

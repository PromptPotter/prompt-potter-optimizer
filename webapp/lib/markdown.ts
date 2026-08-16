import { Marked } from "marked";

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Markdown → HTML for artifacts we did not write.
 *
 * The viewer renders campaign artifacts — `log.md`, `review.md` — and those quote LLM output,
 * which in turn quotes sample transcripts, i.e. rows from a dataset any signed-up tenant can
 * upload. Markdown passes raw HTML through by default and `marked` has had no sanitize option
 * since v5, so `marked.parse` + `dangerouslySetInnerHTML` executed whatever a row contained, in
 * the session of whoever opened the file. That reader is usually the host admin.
 *
 * Raw HTML is neutralised by rendering it as the literal text it is in the file. Both HTML token
 * kinds route through `renderer.html` — `Tokens.HTML` is the block form (`<div>…`) and
 * `Tokens.Tag` the inline one (`a <b> word`) — so one override covers both; overriding only the
 * block half leaves every inline `<img onerror>` live.
 *
 * A private `Marked` instance, not `marked.use(...)`: the latter mutates the module-global
 * singleton, so any other caller in this app would silently inherit the escaping (or, worse,
 * lose it if load order changed). Escaping is done at the RENDERER rather than by pre-escaping
 * the source, because pre-escaping reaches inside fenced code blocks — which marked already
 * escapes — and surfaces `&amp;lt;` where the file plainly says `<`.
 */
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

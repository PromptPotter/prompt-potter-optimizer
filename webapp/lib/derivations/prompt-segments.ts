// A prompt field read as the snippets it holds and the free text around them. The wire stays one
// string: a snippet is recognized by its content (its id IS its content) standing on its own lines.
// The shape is always free, (snippet, free)*: a box to write in before, between and after every
// snippet, so what is rendered never changes with what is typed.

import type { LibrarySnippet } from "@/lib/api";

export interface KnownSnippet {
  text: string;
  // The library snippet it is; null for an edit typed here that the library does not hold yet.
  id: string | null;
  // The snippet it was edited from; null for one nobody edited.
  supersedes: string | null;
}

// The first snippet of a lineage: an edit names the source of what it was edited from.
export function lineageRoot(
  snippets: readonly LibrarySnippet[],
  id: string,
): LibrarySnippet | undefined {
  const seen = new Set<string>();
  let at = snippets.find((s) => s.id === id);
  while (at !== undefined && at.supersedes !== null && !seen.has(at.id)) {
    seen.add(at.id);
    const parent: string = at.supersedes;
    const next = snippets.find((s) => s.id === parent);
    if (next === undefined) return at;
    at = next;
  }
  return at;
}

// `raw` is the exact text between its neighbours, the newline joining each snippet included.
export type Segment =
  | { kind: "free"; raw: string; after: boolean; before: boolean }
  | { kind: "snippet"; text: string; snippet: KnownSnippet };

// The earliest occurrence of a known text standing on its own lines, the longest at one index.
function nextHit(
  text: string,
  known: readonly KnownSnippet[],
  from: number,
): { at: number; snippet: KnownSnippet } | null {
  let best: { at: number; snippet: KnownSnippet } | null = null;
  for (const k of known) {
    for (let at = text.indexOf(k.text, from); at !== -1; at = text.indexOf(k.text, at + 1)) {
      const end = at + k.text.length;
      if ((at === 0 || text[at - 1] === "\n") && (end === text.length || text[end] === "\n")) {
        if (!best || at < best.at || (at === best.at && k.text.length > best.snippet.text.length)) {
          best = { at, snippet: k };
        }
        break;
      }
    }
  }
  return best;
}

// `joinSegments(segmentPrompt(t, k)) === t` for every `t`.
export function segmentPrompt(text: string, known: readonly KnownSnippet[]): Segment[] {
  const out: Segment[] = [];
  let pos = 0;
  for (let hit = nextHit(text, known, 0); hit; hit = nextHit(text, known, pos)) {
    out.push({ kind: "free", raw: text.slice(pos, hit.at), after: pos > 0, before: true });
    out.push({ kind: "snippet", text: hit.snippet.text, snippet: hit.snippet });
    pos = hit.at + hit.snippet.text.length;
  }
  out.push({ kind: "free", raw: text.slice(pos), after: pos > 0, before: false });
  return out;
}

export function joinSegments(segments: readonly Segment[]): string {
  return segments.map((s) => (s.kind === "free" ? s.raw : s.text)).join("");
}

// What a free box shows: its text without the newlines that join it to a snippet.
export function freeText(s: Extract<Segment, { kind: "free" }>): string {
  let t = s.raw;
  if (s.after && t.startsWith("\n")) t = t.slice(1);
  if (s.before && t.endsWith("\n")) t = t.slice(0, -1);
  return t;
}

// A free box's text written back with the joining newlines a snippet beside it needs.
export function withFreeText(
  s: Extract<Segment, { kind: "free" }>,
  text: string,
): Extract<Segment, { kind: "free" }> {
  if (text === "") return { ...s, raw: s.after && s.before ? "\n" : "" };
  return { ...s, raw: `${s.after ? "\n" : ""}${text}${s.before ? "\n" : ""}` };
}

// An edited snippet's text keeps the newlines that make it its own lines.
export function withSnippetText(
  segments: readonly Segment[],
  i: number,
  text: string,
): Segment[] {
  return segments.map((s, j) => (j === i && s.kind === "snippet" ? { ...s, text } : s));
}

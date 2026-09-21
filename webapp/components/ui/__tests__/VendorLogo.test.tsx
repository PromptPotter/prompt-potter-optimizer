// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { VendorLogo, VendorSprite, vendorLabel } from "../VendorLogo";

afterEach(cleanup);

describe("VendorLogo", () => {
  // `<use href="#id">` resolves within the DOCUMENT, so a mark whose symbol is not mounted
  // draws NOTHING and says nothing about it. This pins the two ends to one id.
  it("points at a symbol the sprite actually declares", () => {
    const { container } = render(
      <>
        <VendorSprite />
        <VendorLogo vendor="openai" />
      </>,
    );
    const href = container.querySelector("use")?.getAttribute("href");
    expect(href).toBe("#pp-vendor-openai");
    expect(container.querySelector(`symbol${href}`)).toBeTruthy();
  });

  it("declares a symbol for every vendor that has geometry, and none for the rest", () => {
    const { container } = render(<VendorSprite />);
    const ids = [...container.querySelectorAll("symbol")].map((s) => s.id);
    expect(ids).toContain("pp-vendor-deepseek");
    expect(ids).toContain("pp-vendor-meta-llama");
    // Monogram-only vendors must NOT declare an empty symbol — a `<use>` at one would draw a
    // hole rather than falling through to the initial.
    expect(ids).not.toContain("pp-vendor-inception");
  });

  // The mark REPLACES the model text on a sidebar row, so it carries the reading and has to be
  // named. An `aria-hidden` mark there would leave the row saying nothing about what it ran.
  it("is named by the models it stands in for, not by decoration", () => {
    render(<VendorLogo vendor="openai" models={["openai/gpt-oss-20b:nitro"]} />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe(
      "OpenAI — openai/gpt-oss-20b:nitro",
    );
  });

  it("falls back to the vendor alone when it stands for no particular id", () => {
    render(<VendorLogo vendor="deepseek" />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("DeepSeek");
  });

  // The set of vendors is OPEN — a namespace nobody has drawn a mark for still has to render
  // something countable, and it must still say which vendor it is.
  it("draws an unknown vendor as its own initial, never as a hole", () => {
    const { container } = render(<VendorLogo vendor="acme-labs" />);
    const mark = screen.getByRole("img");
    expect(mark.getAttribute("aria-label")).toBe("acme-labs");
    expect(mark.textContent).toBe("A");
    expect(container.querySelector("use")).toBeNull();
  });

  // `inception` and `inclusionai` are two live brands sharing the initial `I`. Both are named
  // in the table now, but the OPEN tail has the same hazard: one shared fallback ink would
  // collapse two unknown vendors into one mark, which is what the column exists to avoid.
  it("gives two unknown vendors sharing an initial different ink", () => {
    const { container } = render(
      <>
        <VendorLogo vendor="acme-labs" />
        <VendorLogo vendor="aurora-ai" />
      </>,
    );
    const [a, b] = [...container.querySelectorAll<HTMLElement>("[role=img]")];
    expect(a?.textContent).toBe(b?.textContent);
    expect(a?.style.getPropertyValue("--vendor-tint")).not.toBe(
      b?.style.getPropertyValue("--vendor-tint"),
    );
  });

  it("names a known vendor the way the brand does, not the way the slug does", () => {
    expect(vendorLabel("meta-llama")).toBe("Meta");
    expect(vendorLabel("mistralai")).toBe("Mistral");
    expect(vendorLabel("acme-labs")).toBe("acme-labs");
  });
});

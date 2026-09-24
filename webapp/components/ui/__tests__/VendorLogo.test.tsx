// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { VendorLogo, VendorSprite, vendorLabel } from "../VendorLogo";

afterEach(cleanup);

describe("VendorLogo", () => {
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
    // An empty symbol would draw a hole rather than falling through to the initial.
    expect(ids).not.toContain("pp-vendor-inception");
  });

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

  it("draws an unknown vendor as its own initial, never as a hole", () => {
    const { container } = render(<VendorLogo vendor="acme-labs" />);
    const mark = screen.getByRole("img");
    expect(mark.getAttribute("aria-label")).toBe("acme-labs");
    expect(mark.textContent).toBe("A");
    expect(container.querySelector("use")).toBeNull();
  });

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

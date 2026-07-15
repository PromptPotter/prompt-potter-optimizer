import { describe, it, expect } from "vitest";
import { overlaySetsModelOutsideAllowed as out } from "../allowedModels";

// Mirrors the Python truth table (`test` intent: the client warning and the server
// babysit gate must agree). A model outside the allow-list taints; a sanctioned one
// is clean; empty allow-list is restrictive (any model steer taints); a non-model
// edit (temperature) never taints; a provider edit always does.
describe("overlaySetsModelOutsideAllowed", () => {
  const allowed = ["openai/gpt-oss-120b"];
  const ds = { l1_generate: { model: "deepseek/deepseek-v4-flash:nitro" } };
  const oss = { l1_generate: { model: "openai/gpt-oss-120b" } };
  const temp = { l1_generate: { temperature: 0.9 } };
  const prov = { l1_generate: { provider: "openrouter" } };

  it("taints a model outside the allow-list", () => expect(out(ds, allowed)).toBe(true));
  it("is clean for a sanctioned model", () => expect(out(oss, allowed)).toBe(false));
  it("is restrictive when the allow-list is empty", () => expect(out(ds, [])).toBe(true));
  it("treats absent allow-list as empty (restrictive)", () => expect(out(ds, null)).toBe(true));
  it("ignores a non-model edit", () => expect(out(temp, allowed)).toBe(false));
  it("always taints a provider edit", () => expect(out(prov, allowed)).toBe(true));
  it("is clean for an empty overlay", () => expect(out({}, allowed)).toBe(false));
  it("handles a null overlay", () => expect(out(null, allowed)).toBe(false));
  it("skips non-object node entries", () =>
    expect(out({ steps: ["a", "b"], l1_generate: oss.l1_generate }, allowed)).toBe(false));
});

import { afterEach, describe, expect, it, vi } from "vitest";

const answering = (build: string | null) =>
  vi.fn(async () => new Response("{}", { headers: build ? { "X-Webapp-Build": build } : {} }));

async function clientBuiltAs(build: string) {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_WEBAPP_BUILD", build);
  return import("@/lib/api/client");
}

describe("the build check on every API answer", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("stays quiet while the server serves this tab's build, or names none", async () => {
    const client = await clientBuiltAs("a1");
    vi.stubGlobal("fetch", answering("a1"));
    await client.jget("/api/v1/x");
    vi.stubGlobal("fetch", answering(null));
    await client.jget("/api/v1/x");
    expect(client.buildIsStale()).toBe(false);
  });

  it("notes a server that moved on, through a read and a write alike, and stays noted", async () => {
    for (const call of ["jget", "jpost"] as const) {
      const client = await clientBuiltAs("a1");
      vi.stubGlobal("fetch", answering("b2"));
      await (call === "jget" ? client.jget("/api/v1/x") : client.jpost("/api/v1/x", {}));
      expect(client.buildIsStale()).toBe(true);
      vi.stubGlobal("fetch", answering("a1"));
      await client.jget("/api/v1/x");
      expect(client.buildIsStale()).toBe(true);
    }
  });

  it("compares nothing in a build that carries no id", async () => {
    const client = await clientBuiltAs("");
    vi.stubGlobal("fetch", answering("b2"));
    await client.jget("/api/v1/x");
    expect(client.buildIsStale()).toBe(false);
  });
});

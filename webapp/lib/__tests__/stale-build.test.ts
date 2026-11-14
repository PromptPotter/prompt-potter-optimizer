import { afterEach, describe, expect, it, vi } from "vitest";
import { SERVED_CONTRACT } from "@/lib/api/types";

const answering = (contract: string | null) =>
  vi.fn(
    async () =>
      new Response("{}", { headers: contract ? { "X-Served-Contract": contract } : {} }),
  );

async function freshClient() {
  vi.resetModules();
  return import("@/lib/api/client");
}

describe("the contract check on every API answer", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stays quiet while the server serves this tab's contract, or names none", async () => {
    const client = await freshClient();
    vi.stubGlobal("fetch", answering(SERVED_CONTRACT));
    await client.jget("/api/v1/x");
    vi.stubGlobal("fetch", answering(null));
    await client.jget("/api/v1/x");
    expect(client.buildIsStale()).toBe(false);
  });

  it("notes a server whose contract moved on, through a read and a write alike, and stays noted", async () => {
    for (const call of ["jget", "jpost"] as const) {
      const client = await freshClient();
      vi.stubGlobal("fetch", answering(`${SERVED_CONTRACT}-moved`));
      await (call === "jget" ? client.jget("/api/v1/x") : client.jpost("/api/v1/x", {}));
      expect(client.buildIsStale()).toBe(true);
      vi.stubGlobal("fetch", answering(SERVED_CONTRACT));
      await client.jget("/api/v1/x");
      expect(client.buildIsStale()).toBe(true);
    }
  });
});

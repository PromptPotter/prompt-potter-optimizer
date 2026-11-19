import { afterEach, describe, expect, it, vi } from "vitest";
import { FakeStream } from "@/lib/__tests__/fake-stream";
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

  it("notes a stream frame stamped with another contract as it notes such an answer", async () => {
    FakeStream.opened = [];
    vi.stubGlobal("EventSource", FakeStream);
    const client = await freshClient();
    const { openLiveStream } = await import("@/lib/live-stream");
    const frames: unknown[] = [];
    openLiveStream("/api/v1/feed", { surface: "feed" }, { frame: (f) => frames.push(f) });
    const [feed] = FakeStream.opened;
    feed?.send({ subject: "account", version: { process: "p1", n: 1 } });
    expect(client.buildIsStale()).toBe(false);
    feed?.send({ subject: "account", version: { process: "p1", n: 1 } }, `${SERVED_CONTRACT}-moved`);
    expect(client.buildIsStale()).toBe(true);
    expect(frames).toHaveLength(2);
  });
});

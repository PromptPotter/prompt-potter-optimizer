import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRead } from "@/lib/hooks/useRead";
import { clearReadCache, type ReadDescriptor } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";
import { BusyPane } from "@/lib/state/busy";

// A pane that draws nothing while its read flies.
function Silent({ read }: { read: ReadDescriptor<string> }) {
  useRead(read);
  return null;
}

describe("BusyPane — a read in flight is its pane's state", () => {
  beforeEach(() => {
    clearReadCache(false);
    vi.stubGlobal("fetch", async () => new Response(null, { status: 401 }));
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("marks the pane it reads under, not another, until the read lands", async () => {
    let answer: (v: string) => void = () => {};
    const read: ReadDescriptor<string> = {
      id: "busy\x1f/x",
      load: () => new Promise((r) => (answer = (data) => r({ kind: "ok", data, validator: null }))),
    };
    const { getByTestId } = render(
      <AuthProvider>
        <BusyPane data-testid="a">
          <Silent read={read} />
        </BusyPane>
        <BusyPane data-testid="b" />
      </AuthProvider>,
    );
    await act(async () => {});
    expect(getByTestId("a").getAttribute("aria-busy")).toBe("true");
    expect(getByTestId("b").getAttribute("aria-busy")).toBeNull();
    await act(async () => answer("x"));
    expect(getByTestId("a").getAttribute("aria-busy")).toBeNull();
  });
});

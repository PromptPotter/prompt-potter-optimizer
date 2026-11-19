import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { clearReadCache } from "@/lib/read-cache";
import { InWorkspace } from "@/lib/test-utils/in-workspace";
import { OWNER_FILABLE, accountKey, couponStatus, providerRoute } from "@/lib/test-fixtures";
import type { CouponStatus } from "@/lib/api/types";
import { ProviderKeys } from "../AccountKeysTab";

interface Sent {
  method: string;
  url: string;
  body: string | null;
}

// The session probe answers the account's owner, who files every key act; a key write answers
// `reply`, and no other read stands.
let probes = 0;

function serve(reply: () => Response): Sent[] {
  const sent: Sent[] = [];
  probes = 0;
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    if (url.endsWith("/auth/me")) {
      probes += 1;
      return Response.json({ tenant_id: "t", user_id: "u", filable: OWNER_FILABLE });
    }
    if ((init.method ?? "GET") === "GET") return new Response(null, { status: 404 });
    sent.push({
      method: init.method ?? "GET",
      url,
      body: typeof init.body === "string" ? init.body : null,
    });
    return reply();
  });
  return sent;
}

// The session's probe adopts the identity, which re-asks every read: the kinds stand after it.
async function view(coupon: CouponStatus) {
  render(
    <InWorkspace>
      <ProviderKeys coupon={coupon} />
    </InWorkspace>,
  );
  await waitFor(() => expect(probes).toBeGreaterThan(1));
}

describe("ProviderKeys", () => {
  beforeEach(() => clearReadCache(true));

  it("shows a held key by its last four alone, and removing it issues the command", async () => {
    const sent = serve(() => Response.json({ command_id: "c1" }, { status: 202 }));
    await view(
      couponStatus({
        routes: [providerRoute("anthropic", accountKey("anthropic", "ab12")), providerRoute("openai")],
      }),
    );

    expect((await screen.findByLabelText("anthropic key ending ab12")).textContent).toBe("…ab12");
    // The held provider offers no field to type into; the unheld one does.
    expect(screen.queryByLabelText("anthropic API key")).toBeNull();
    expect(await screen.findByLabelText("openai API key")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Remove the anthropic key" }));
    });
    expect(sent).toEqual([
      {
        method: "POST",
        url: "/api/v1/commands/remove-api-key",
        body: JSON.stringify({ provider: "anthropic" }),
      },
    ]);
  });

  it("sends a pasted key once, on Enter, as the command body", async () => {
    const sent = serve(() => Response.json(accountKey("openai", "wxyz")));
    await view(couponStatus());

    const field = await screen.findByLabelText("openai API key");
    expect(field.getAttribute("type")).toBe("password");
    fireEvent.change(field, { target: { value: "sk-test-wxyz" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    expect(sent).toEqual([
      {
        method: "POST",
        url: "/api/v1/commands/set-api-key",
        body: JSON.stringify({ provider: "openai", secret: "sk-test-wxyz" }),
      },
    ]);
  });

  it("renders the server's own sentence for a refused key", async () => {
    const message = "A openai key is one unbroken string of at most 512 characters.";
    serve(() =>
      Response.json(
        { code: "api_key_malformed", error_id: "e1", details: {}, message },
        { status: 422 },
      ),
    );
    await view(couponStatus());

    const field = await screen.findByLabelText("openai API key");
    fireEvent.change(field, { target: { value: "sk-bad" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    expect(screen.getByRole("alert").textContent).toBe(message);
  });
});

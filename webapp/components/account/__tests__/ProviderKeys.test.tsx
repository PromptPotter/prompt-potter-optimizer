import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/state/auth";
import { clearReadCache } from "@/lib/read-cache";
import { accountKey, couponStatus } from "@/lib/test-fixtures";
import type { CouponStatus } from "@/lib/api";
import { ProviderKeys } from "../AccountKeysTab";

interface Sent {
  method: string;
  url: string;
  body: string | null;
}

// The session probe answers 401 (a write needs no session on the client); a key write answers `reply`.
function serve(reply: () => Response): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    if (url.endsWith("/auth/me")) return new Response(null, { status: 401 });
    sent.push({
      method: init.method ?? "GET",
      url,
      body: typeof init.body === "string" ? init.body : null,
    });
    return reply();
  });
  return sent;
}

function view(coupon: CouponStatus) {
  return render(
    <AuthProvider>
      <ProviderKeys coupon={coupon} />
    </AuthProvider>,
  );
}

describe("ProviderKeys", () => {
  beforeEach(clearReadCache);

  it("shows a held key by its last four alone, and removing it issues the DELETE", async () => {
    const sent = serve(() => new Response(null, { status: 204 }));
    view(couponStatus({ keys: [accountKey("anthropic", "ab12")] }));

    expect(screen.getByLabelText("anthropic key ending ab12").textContent).toBe("…ab12");
    // The held provider offers no field to type into; the unheld one does.
    expect(screen.queryByLabelText("anthropic API key")).toBeNull();
    expect(screen.getByLabelText("openai API key")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Remove the anthropic key" }));
    });
    expect(sent).toEqual([{ method: "DELETE", url: "/api/v1/auth/api-keys/anthropic", body: null }]);
  });

  it("sends a pasted key once, on Enter, as the PUT body", async () => {
    const sent = serve(() => Response.json(accountKey("openai", "wxyz")));
    view(couponStatus());

    const field = screen.getByLabelText("openai API key");
    expect(field.getAttribute("type")).toBe("password");
    fireEvent.change(field, { target: { value: "sk-test-wxyz" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    expect(sent).toEqual([
      {
        method: "PUT",
        url: "/api/v1/auth/api-keys/openai",
        body: JSON.stringify({ secret: "sk-test-wxyz" }),
      },
    ]);
  });

  it("renders the server's own sentence for a refused key", async () => {
    const message = "A openai key is one unbroken string of at most 512 characters.";
    serve(() =>
      Response.json(
        { error: "api_key_malformed", error_id: "e1", details: null, message },
        { status: 422 },
      ),
    );
    view(couponStatus());

    const field = screen.getByLabelText("openai API key");
    fireEvent.change(field, { target: { value: "sk-bad" } });
    await act(async () => {
      fireEvent.keyDown(field, { key: "Enter" });
    });
    expect(screen.getByRole("alert").textContent).toBe(message);
  });

  it("reads a used-up coupon as used up", () => {
    serve(() => new Response(null, { status: 500 }));
    view(couponStatus({ left_usd: 0, used_share: 1, exhausted: true }));
    expect(screen.getByText(/The host's allowance is used up\./)).toBeTruthy();
  });
});

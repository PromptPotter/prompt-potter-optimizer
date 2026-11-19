import { createElement, type ReactNode } from "react";
import { AuthProvider } from "@/lib/state/auth";
import { ViewMemoryProvider } from "@/lib/state/view-memory";
import { WorkspaceProvider } from "@/lib/state/workspace";

// The real providers a viewed surface reads through, never a mock of one (`vitest.config.ts` shares
// one module graph, so a mock misses a module another file loaded first): the test's `fetch` and
// address bar are the server and the link they read.
export function InWorkspace({ children }: { children: ReactNode }) {
  return createElement(
    AuthProvider,
    null,
    createElement(ViewMemoryProvider, null, createElement(WorkspaceProvider, null, children)),
  );
}

// An anonymous visitor: `/auth/me` and every other read answer 401, so no feed opens (I5).
export const anonymous = async (): Promise<Response> => new Response(null, { status: 401 });

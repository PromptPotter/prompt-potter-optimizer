"use client";
// Auth context — single global probe of `/api/v1/auth/me`.
//
// It also owns the ANON ENTRY PROMPT: whether the sign-in modal is open, and the
// error a failed OIDC callback bounced back with. Both live here because the
// prompt has several triggers (a Log in chip in the sidebar footer, the same
// chip in the mobile app bar, the `?auth_error=` redirect) and exactly one
// modal — `<WelcomeLockoutModal>` in app/page.tsx. A per-trigger copy of the
// modal is how two of them drift.
//
// Three states:
//   loading  — initial probe in flight (also after a focus revalidation
//              while we wait for the response)
//   authed   — `/auth/me` returned 200, `me` carries the envelope
//   unauthed — `/auth/me` returned non-200 (typically 401) or threw
//
// Probes on mount + on window focus so a successful OIDC callback in
// another tab cleanly flips the lockout away on return.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError, fetchMe, type MeResponse } from "@/lib/api";

export type AuthStatus = "loading" | "authed" | "unauthed";

// What the one sign-in modal needs to render. `code`/`email` are non-null only
// after an OIDC callback bounced back with a failure.
export interface AuthPrompt {
  open: boolean;
  code: string | null;
  email: string | null;
}

const PROMPT_CLOSED: AuthPrompt = { open: false, code: null, email: null };

interface AuthCtx {
  status: AuthStatus;
  me: MeResponse | null;
  refresh: () => void;
  authPrompt: AuthPrompt;
  openAuthPrompt: () => void;
  closeAuthPrompt: () => void;
}

const AuthContext = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [me, setMe] = useState<MeResponse | null>(null);
  const [authPrompt, setAuthPrompt] = useState<AuthPrompt>(PROMPT_CLOSED);
  // Bumped to force re-probes; the effect below depends on it.
  const [nonce, setNonce] = useState(0);
  // Latest probe wins — older in-flight responses are dropped if a newer
  // one started.
  const probeIdRef = useRef(0);

  const refresh = useCallback(() => {
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    const probeId = probeIdRef.current + 1;
    probeIdRef.current = probeId;
    let cancelled = false;
    fetchMe()
      .then((data) => {
        if (cancelled || probeIdRef.current !== probeId) return;
        setMe(data);
        setStatus("authed");
      })
      .catch(() => {
        if (cancelled || probeIdRef.current !== probeId) return;
        setMe(null);
        setStatus("unauthed");
      });
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  useEffect(() => {
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refresh]);

  const openAuthPrompt = useCallback(
    () => setAuthPrompt({ open: true, code: null, email: null }),
    [],
  );
  const closeAuthPrompt = useCallback(() => setAuthPrompt(PROMPT_CLOSED), []);

  // OIDC callback bounce-back: /auth/callback/{provider} 303s to
  // /?auth_error=<code>(&email=<addr>) on failure. Open the prompt with the
  // error banner, then strip the params from the visible URL so a refresh
  // doesn't replay. Read window.location directly (not useSearchParams) to
  // avoid the Suspense requirement that breaks static export. Same sanctioned
  // set-state-in-effect pattern as `lib/workspace.tsx` deep-link hydration:
  // SSR renders empty, client effect corrects post-hydration.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("auth_error");
    if (!code) return;
    setAuthPrompt({ open: true, code, email: url.searchParams.get("email") });
    url.searchParams.delete("auth_error");
    url.searchParams.delete("email");
    window.history.replaceState({}, "", url.toString());
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <AuthContext.Provider
      value={{ status, me, refresh, authPrompt, openAuthPrompt, closeAuthPrompt }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthCtx {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

// Poll gate. Every protected `/api/v1/*` read 401s without a session, so a
// poll loop must (1) not run while unauthed and (2) detect a session that
// died mid-run. `authed` gates `usePoll`'s `enabled`; `onAuthError`, called
// from a tick's catch, re-probes `/auth/me` when a read 401s — confirming the
// dead session and flipping `status` to "unauthed", which drops `authed` and
// halts the loop. Without this a tab whose session died (e.g. server restart)
// would 401-storm forever, since `/auth/me` is otherwise only re-probed on
// window focus. Reads throw `ApiError` carrying `.status` (lib/api/client.ts::jget).
export function useAuthGate(): {
  authed: boolean;
  onAuthError: (err: unknown) => void;
} {
  const { status, refresh } = useAuth();
  const onAuthError = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) refresh();
    },
    [refresh],
  );
  return { authed: status === "authed", onAuthError };
}

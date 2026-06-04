"use client";
// Auth context — single global probe of `/api/v1/auth/me`.
//
// Drives the WelcomeLockoutModal in app/layout.tsx. Other widgets (Topbar
// account chip, etc.) can `useAuth()` once they want to react to identity
// without their own fetch.
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

interface AuthCtx {
  status: AuthStatus;
  me: MeResponse | null;
  refresh: () => void;
}

const AuthContext = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [me, setMe] = useState<MeResponse | null>(null);
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

  return (
    <AuthContext.Provider value={{ status, me, refresh }}>{children}</AuthContext.Provider>
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

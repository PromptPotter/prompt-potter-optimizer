"use client";
// The single `/auth/me` probe, re-run on window focus so an OIDC login in another tab lands.
// It also owns the ONE sign-in modal's state (`<WelcomeLockoutModal>`), whatever triggers it.

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

// `code`/`email` are non-null only after an OIDC callback bounced back with a failure.
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
  const [nonce, setNonce] = useState(0);
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

  // `/auth/callback/{provider}` 303s to `/?auth_error=<code>(&email=)` on failure. Read
  // `window.location`, not `useSearchParams`, whose Suspense requirement breaks static export.
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

// One spelling for who steered a fork: two panels naming the same operator differently is a
// lineage that cannot be joined on.
export function steeredBy(me: MeResponse | null): string | undefined {
  return me?.name || me?.email || me?.user_id || undefined;
}

// `authed` gates a poll's `enabled`; `onAuthError`, from a tick's catch, re-probes on a 401 so a
// session that died mid-run halts the loop instead of 401-storming until the next focus.
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

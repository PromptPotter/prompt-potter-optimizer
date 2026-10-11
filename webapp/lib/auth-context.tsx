"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError, failureKind, meRead, type MeResponse } from "@/lib/api";
import { reportIncident } from "@/lib/diagnostics";
import { clearReadCache, readThrough } from "@/lib/read-cache";

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
  // Every cached read belongs to one identity; `undefined` is "not probed yet".
  const identityRef = useRef<string | null | undefined>(undefined);

  const refresh = useCallback(() => {
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    const probeId = probeIdRef.current + 1;
    probeIdRef.current = probeId;
    let cancelled = false;
    const adopt = (identity: string | null) => {
      if (identityRef.current === identity) return;
      identityRef.current = identity;
      clearReadCache();
    };
    // The probe `useRead`'s own gate stands on, so it cannot ride the hook; it shares the cache.
    const probe = meRead();
    readThrough(probe.id, probe.load, new AbortController().signal)
      .then((data) => {
        if (cancelled || probeIdRef.current !== probeId) return;
        adopt(`${data.tenant_id}\x1f${data.user_id}`);
        setMe(data);
        setStatus("authed");
      })
      .catch((e: unknown) => {
        if (cancelled || probeIdRef.current !== probeId) return;
        // A 401 is the probe's ordinary answer; anything else is an incident that reads as anon.
        if (failureKind(e) !== "auth") reportIncident(e, { surface: "me" });
        adopt(null);
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

// `onAuthError` re-probes on a 401, so a session that died mid-run halts the polls.
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

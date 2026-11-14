import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { failureKind, failurePasses, meRead } from "@/lib/api";
import { type MeResponse, SIGN_IN_REFUSAL_INFO } from "@/lib/api/types";
import { RECONNECT_POLL_MS } from "@/lib/cadence";
import { clearReadCache, readThrough } from "@/lib/read-cache";

type AuthStatus = "loading" | "authed" | "unauthed";
export type SignInRefusal = keyof typeof SIGN_IN_REFUSAL_INFO;

interface AuthPrompt {
  open: boolean;
  refusal: SignInRefusal | null;
}

const PROMPT_CLOSED: AuthPrompt = { open: false, refusal: null };

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
    let retry: ReturnType<typeof setTimeout> | undefined;
    const adopt = (identity: string | null) => {
      if (identityRef.current === identity) return;
      identityRef.current = identity;
      clearReadCache(identity !== null);
    };
    // The probe `useRead`'s own gate stands on, so it cannot ride the hook; it shares the cache.
    readThrough(meRead(), new AbortController().signal)
      .then((data) => {
        if (cancelled || probeIdRef.current !== probeId) return;
        adopt(`${data.tenant_id}\x1f${data.user_id}`);
        setMe(data);
        setStatus("authed");
      })
      .catch((e: unknown) => {
        if (cancelled || probeIdRef.current !== probeId) return;
        // The cache reported the failure (`read-cache.ts::fail`); this reacts to it alone.
        const kind = failureKind(e);
        // A failure that passes says nothing of the session: a probed one keeps its identity and
        // cache; an unprobed one stays `loading` (never anon, so a signed-in reload during an
        // outage or a throttle is not signed out) and re-probes on the reconnect beat (I7).
        if (failurePasses(kind)) {
          if (identityRef.current === undefined) {
            retry = setTimeout(refresh, RECONNECT_POLL_MS);
          }
          return;
        }
        adopt(null);
        setMe(null);
        setStatus("unauthed");
      });
    return () => {
      cancelled = true;
      clearTimeout(retry);
    };
  }, [nonce, refresh]);

  useEffect(() => {
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refresh]);

  const openAuthPrompt = useCallback(
    () => setAuthPrompt({ open: true, refusal: null }),
    [],
  );
  const closeAuthPrompt = useCallback(() => setAuthPrompt(PROMPT_CLOSED), []);

  // The sign-in outcome rides the query string; read it once on mount, then strip it.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("auth_error");
    if (!code) return;
    // A code the callback never mints opens the prompt without a line.
    const minted = Object.hasOwn(SIGN_IN_REFUSAL_INFO, code);
    setAuthPrompt({ open: true, refusal: minted ? (code as SignInRefusal) : null });
    url.searchParams.delete("auth_error");
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
      if (failureKind(err) === "auth") refresh();
    },
    [refresh],
  );
  return { authed: status === "authed", onAuthError };
}

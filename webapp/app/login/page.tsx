"use client";

import { useEffect, useState } from "react";

type ProvidersResponse = { providers: string[] };

const PROVIDER_LABEL: Record<string, string> = {
  google: "Sign in with Google",
  github: "Sign in with GitHub",
};

export default function LoginPage() {
  const [providers, setProviders] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/v1/auth/providers", { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`providers endpoint returned ${r.status}`);
        return r.json() as Promise<ProvidersResponse>;
      })
      .then((data) => {
        if (!cancelled) setProviders(data.providers);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="login-container">
      <h1>PromptPotter</h1>
      <p className="login-sub">Sign in to continue</p>
      {error ? <p className="login-error">{error}</p> : null}
      {providers === null && error === null ? (
        <p className="login-loading">Loading providers…</p>
      ) : null}
      {providers !== null && providers.length === 0 ? (
        <p className="login-empty">
          No OIDC providers configured. Add credentials to{" "}
          <code>.promptpotter/identity/oidc.json</code>.
        </p>
      ) : null}
      <div className="login-buttons">
        {(providers ?? []).map((name) => (
          <a key={name} className="login-button" href={`/api/v1/auth/login/${name}`}>
            {PROVIDER_LABEL[name] ?? `Sign in with ${name}`}
          </a>
        ))}
      </div>
    </main>
  );
}

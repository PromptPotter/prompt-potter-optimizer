"use client";

import { useEffect, useState } from "react";
import { BRAND } from "@/lib/brand";

type ProvidersResponse = { providers: string[] };

const PROVIDER_LABEL: Record<string, string> = {
  google: "Sign in with Google",
  github: "Sign in with GitHub",
};

// Bare host for the card's URL line ("promptpotter.com"). Falls back to the
// raw string if it isn't a parseable URL — the value is build-time config.
function siteHost(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

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
      {BRAND.marketing.url ? (
        <>
          <div className="login-or" role="separator" aria-label="or">
            <span>or</span>
          </div>
          {/* Bookmark/embed card linking home to the origin marketing site —
              the same shape a Notion paste of the URL renders. Hidden when a
              whitelabel host clears NEXT_PUBLIC_MARKETING_URL. */}
          <a
            className="login-card"
            href={BRAND.marketing.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <span className="login-card-body">
              <span className="login-card-title">{BRAND.marketing.title}</span>
              <span className="login-card-tagline">{BRAND.marketing.tagline}</span>
              <span className="login-card-url">{siteHost(BRAND.marketing.url)}</span>
            </span>
            <img
              className="login-card-mark"
              src="/brand/potter-mark.svg"
              alt=""
              width={48}
              height={48}
            />
          </a>
        </>
      ) : null}
    </main>
  );
}

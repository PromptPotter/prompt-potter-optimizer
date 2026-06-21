"use client";
import { useEffect, useState } from "react";
import { RunningJobsButton } from "./RunningJobsButton";
import { AccountModal } from "@/components/account/AccountModal";
import { WelcomeLockoutModal } from "@/components/onboarding/WelcomeLockoutModal";
import { useAuth } from "@/lib/auth-context";
import { applyTheme, readStoredTheme, type Theme } from "@/lib/theme";

function ThemeToggle() {
  const flip = () => {
    const next: Theme = readStoredTheme() === "light" ? "dark" : "light";
    applyTheme(next);
  };

  return (
    <button
      className="theme-toggle"
      type="button"
      onClick={flip}
      title="Toggle bright / dark theme"
      aria-label="Toggle theme"
    >
      <svg className="sun" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
        <circle cx="8" cy="8" r="3" />
        <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.4 1.4M11.55 11.55l1.4 1.4M3.05 12.95l1.4-1.4M11.55 4.45l1.4-1.4" />
      </svg>
      <svg className="moon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
        <path d="M6 1.5A6.5 6.5 0 1 0 14.5 10 5 5 0 0 1 6 1.5z" />
      </svg>
    </button>
  );
}

// Per-cycle sub-tabs (Replit-style): the sidebar carries the campaign
// library; the topbar carries the views over the *currently-selected*
// campaign — Chat, Dashboard, Verify, and the Files tree.
export type Tab = "chat" | "dashboard" | "files" | "verify";

interface Props {
  tab: Tab;
  onTabChange: (t: Tab) => void;
  // Mobile-only — when present, renders a hamburger on the left of the
  // topbar that toggles the Sidebar drawer. Desktop hides it via CSS.
  onMenuToggle?: () => void;
}

const TABS: { id: Tab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "dashboard", label: "Dashboard" },
  { id: "verify", label: "Verify" },
  { id: "files", label: "Files" },
];

export function Topbar({ tab, onTabChange, onMenuToggle }: Props) {
  const [accountOpen, setAccountOpen] = useState(false);
  const [authPromptOpen, setAuthPromptOpen] = useState(false);
  const [authErrorCode, setAuthErrorCode] = useState<string | null>(null);
  const [authErrorEmail, setAuthErrorEmail] = useState<string | null>(null);
  const { status } = useAuth();

  // OIDC callback bounce-back: /auth/callback/{provider} 303s to
  // /?auth_error=<code>(&email=<addr>) on failure. Auto-open the
  // sign-in modal with the error banner, then strip the params from the
  // visible URL so a refresh doesn't replay. Read window.location
  // directly (not useSearchParams) to avoid the Suspense requirement
  // that breaks static export. Same sanctioned set-state-in-effect
  // pattern as `lib/workspace.tsx` deep-link hydration: SSR renders
  // empty, client effect corrects post-hydration.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("auth_error");
    if (!code) return;
    setAuthErrorCode(code);
    setAuthErrorEmail(url.searchParams.get("email"));
    setAuthPromptOpen(true);
    url.searchParams.delete("auth_error");
    url.searchParams.delete("email");
    window.history.replaceState({}, "", url.toString());
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <header className="topbar">
      {onMenuToggle ? (
        <button
          type="button"
          className="topbar-menu"
          aria-label="Open campaign menu"
          onClick={onMenuToggle}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
            <line x1="3" y1="6" x2="17" y2="6" />
            <line x1="3" y1="10" x2="17" y2="10" />
            <line x1="3" y1="14" x2="17" y2="14" />
          </svg>
        </button>
      ) : null}
      {/* Search — disabled placeholder (M13+ scope): a single magnifying-glass
          affordance at every width, no input field. Stays in the DOM per the
          placeholder rule; the full search lands later. */}
      <button
        type="button"
        className="topbar-search-icon"
        aria-label="Search analytics (coming soon)"
        title="Search analytics"
        disabled
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
          <circle cx="8" cy="8" r="5" />
          <line x1="12" y1="12" x2="15" y2="15" />
        </svg>
      </button>
      <div className="tabs" role="tablist" aria-label="Campaign view">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`tab${tab === t.id ? " active" : ""}`}
            role="tab"
            tabIndex={0}
            aria-selected={tab === t.id}
            onClick={() => onTabChange(t.id)}
          >{t.label}</button>
        ))}
      </div>
      <RunningJobsButton onPicked={() => onTabChange("dashboard")} />
      <ThemeToggle />
      {status === "authed" ? (
        <>
          <button
            type="button"
            className="account-trigger"
            aria-label="Open account"
            onClick={() => setAccountOpen(true)}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
              <circle cx="8" cy="5.5" r="2.5" />
              <path d="M2.5 14c.8-2.5 3-4 5.5-4s4.7 1.5 5.5 4" />
            </svg>
          </button>
          <AccountModal open={accountOpen} onClose={() => setAccountOpen(false)} />
        </>
      ) : null}
      {status === "unauthed" ? (
        <>
          <button
            type="button"
            className="auth-chip auth-chip-gold"
            onClick={() => setAuthPromptOpen(true)}
          >
            Log in
          </button>
          <button
            type="button"
            className="auth-chip auth-chip-rust"
            onClick={() => setAuthPromptOpen(true)}
          >
            Sign up for free
          </button>
          <WelcomeLockoutModal
            open={authPromptOpen}
            onClose={() => {
              setAuthPromptOpen(false);
              setAuthErrorCode(null);
              setAuthErrorEmail(null);
            }}
            errorCode={authErrorCode}
            errorEmail={authErrorEmail}
          />
        </>
      ) : null}
    </header>
  );
}

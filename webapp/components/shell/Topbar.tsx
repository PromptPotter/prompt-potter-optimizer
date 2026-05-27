"use client";
import { useState } from "react";
import { ThemeToggle } from "./ThemeToggle";
import { AccountModal } from "@/components/account/AccountModal";
import { WelcomeLockoutModal } from "@/components/onboarding/WelcomeLockoutModal";
import { useAuth } from "@/lib/auth-context";

// Per-cycle sub-tabs (Replit-style): the sidebar carries the campaign
// library; the topbar carries the views over the *currently-selected*
// campaign. `files` is part of the type but not rendered here — the
// StatusBar's "Open files" link is the sole entry point into FilesPane.
export type Tab = "chat" | "dashboard" | "files" | "verify";

interface Props {
  tab: Tab;
  onTabChange: (t: Tab) => void;
}

const TABS: { id: Tab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "dashboard", label: "Dashboard" },
  { id: "verify", label: "Verify" },
];

export function Topbar({ tab, onTabChange }: Props) {
  const [accountOpen, setAccountOpen] = useState(false);
  const [authPromptOpen, setAuthPromptOpen] = useState(false);
  const { status } = useAuth();

  return (
    <header className="topbar">
      <input className="search" placeholder="Search analytics..." disabled aria-label="Search analytics" />
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
            onClose={() => setAuthPromptOpen(false)}
          />
        </>
      ) : null}
    </header>
  );
}

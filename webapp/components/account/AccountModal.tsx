"use client";
// Clerk-style account modal — two-pane shell over `/auth/*`. Each section is its
// own pane component: Profile / Security / Activity / Preferences / About.
//
// Update profile, remove account and connect account are INTENTIONAL placeholders, previewing the
// config-edit surface — not scaffolding, and out of scope for any "hide non-functional controls"
// sweep. Peers: `chat/ChatPane.tsx`, `shell/Sidebar.tsx`.

import { AboutUnit } from "./AboutUnit";
import { AccountProfileTab } from "./AccountProfileTab";
import { AccountSecurityTab } from "./AccountSecurityTab";
import { AccountActivityTab } from "./AccountActivityTab";
import { AccountPreferencesTab } from "./AccountPreferencesTab";
import { WorkspaceStoragePanel } from "./WorkspaceStoragePanel";
import { useFetch } from "@/lib/hooks/useFetch";
import { useDialogA11y } from "@/lib/hooks/useDialogA11y";
import { fetchMe } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { DEFAULT_ACCOUNT_PANE, type AccountPane } from "@/lib/view-tab";

interface Props {
  open: boolean;
  onClose: () => void;
}

// The pane set is `lib/view-tab.ts::AccountPane` — it is on the address
// (`#/account/activity`), so the closed set cannot live inside the component that
// renders it. The titles stay here; they are presentation.
const TAB_TITLES: Record<AccountPane, string> = {
  profile: "Profile details",
  security: "Security",
  activity: "Activity",
  storage: "Storage",
  preferences: "Preferences",
  about: "About this unit",
};

export function AccountModal({ open, onClose }: Props) {
  // Which pane, from the address. Nav clicks write it back, so a pane is linkable and
  // survives a reload — the same rule the main view axis follows.
  const { accountPane, openAccount } = useWorkspace();
  const tab: AccountPane = accountPane ?? DEFAULT_ACCOUNT_PANE;
  const setTab = openAccount;
  // `open` in the deps key means useFetch re-runs (and blanks me/error) on every
  // open — stale data from a prior session never flashes in, so no separate reset.
  const { data: me, error } = useFetch(open ? () => fetchMe() : null, [open]);
  // ESC + focus-trap + focus-restore from the shared hook; this modal keeps its
  // own two-pane .account-modal layout rather than Dialog's confirm-card.
  const cardRef = useDialogA11y(open, onClose);

  if (!open) return null;

  return (
    <div
      className="account-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Account"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div ref={cardRef} className="account-modal">
        <nav className="account-nav" aria-label="Account sections">
          <div className="account-nav-head">
            <h2>Account</h2>
            <p>Manage your account info.</p>
          </div>
          <ul className="account-nav-list">
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "profile" ? " active" : ""}`}
                onClick={() => setTab("profile")}
              >
                Profile
              </button>
            </li>
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "security" ? " active" : ""}`}
                onClick={() => setTab("security")}
              >
                Security
              </button>
            </li>
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "activity" ? " active" : ""}`}
                onClick={() => setTab("activity")}
              >
                Activity
              </button>
            </li>
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "storage" ? " active" : ""}`}
                onClick={() => setTab("storage")}
              >
                Storage
              </button>
            </li>
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "preferences" ? " active" : ""}`}
                onClick={() => setTab("preferences")}
              >
                Preferences
              </button>
            </li>
            <li>
              <button
                type="button"
                className={`account-nav-item${tab === "about" ? " active" : ""}`}
                onClick={() => setTab("about")}
              >
                About this unit
              </button>
            </li>
          </ul>
        </nav>
        <section className="account-pane">
          <header className="account-pane-head">
            <h3>{TAB_TITLES[tab]}</h3>
            <button
              type="button"
              className="account-close"
              aria-label="Close"
              onClick={onClose}
            >
              ×
            </button>
          </header>
          <div className="account-pane-body">
            {error ? <p className="account-error">{error}</p> : null}
            {!me && !error && (tab === "profile" || tab === "security") ? (
              <p className="account-loading">Loading…</p>
            ) : null}
            {me && tab === "profile" ? <AccountProfileTab me={me} /> : null}
            {me && tab === "security" ? <AccountSecurityTab me={me} /> : null}
            {tab === "activity" ? <AccountActivityTab /> : null}
            {tab === "storage" ? <WorkspaceStoragePanel /> : null}
            {tab === "preferences" ? <AccountPreferencesTab /> : null}
            {tab === "about" ? <AboutUnit /> : null}
          </div>
        </section>
      </div>
    </div>
  );
}

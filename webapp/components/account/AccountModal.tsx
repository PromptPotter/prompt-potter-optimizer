"use client";
// Update profile, remove account and connect account are INTENTIONAL placeholders: never hide them.

import { useEffect, useRef } from "react";
import { AboutUnit } from "./AboutUnit";
import { AccountFailure, AccountLoading } from "./AccountSection";
import { AccountProfileTab } from "./AccountProfileTab";
import { AccountUsageTab } from "./AccountUsageTab";
import { AccountActivityTab } from "./AccountActivityTab";
import { AccountPreferencesTab } from "./AccountPreferencesTab";
import { WorkspaceStoragePanel } from "./WorkspaceStoragePanel";
import { Button, Dialog, IconClose } from "@/components/ui";
import { cx } from "@/lib/cx";
import { meRead } from "@/lib/api";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useWorkspace } from "@/lib/workspace";
import { DEFAULT_ACCOUNT_PANE, type AccountPane } from "@/lib/view-tab";

const TAB_TITLES: Record<AccountPane, string> = {
  profile: "Profile",
  usage: "Usage & limits",
  activity: "Activity",
  storage: "Storage",
  preferences: "Preferences",
  about: "About this unit",
};

const NAV_GROUPS: readonly { label: string; panes: readonly AccountPane[] }[] = [
  { label: "Account", panes: ["profile", "usage", "activity", "preferences"] },
  { label: "Workspace", panes: ["storage"] },
  { label: "This unit", panes: ["about"] },
];

export function AccountModal() {
  const { accountPane, openAccount, closeAccount: onClose } = useWorkspace();
  const open = accountPane != null;
  const tab: AccountPane = accountPane ?? DEFAULT_ACCOUNT_PANE;
  const profile = useRead(open ? meRead() : null);
  const me = readyData(profile);
  const cardRef = useRef<HTMLDivElement>(null);
  // Runs after Dialog's own first-focusable focus, which would ring "Profile" whatever pane shows.
  useEffect(() => {
    if (open) cardRef.current?.querySelector<HTMLElement>('[aria-current="page"]')?.focus();
  }, [open]);

  if (!open) return null;

  const who = me ? (me.name ?? me.email ?? (me.provider ? me.user_id : "Local workspace")) : null;

  return (
    <Dialog open title="Account" onClose={onClose} bare>
      <div ref={cardRef} className="account-modal">
        <nav className="account-nav" aria-label="Account sections">
          <div className="account-nav-head">
            <h2>Account</h2>
            <p title={who ?? undefined}>{who ?? " "}</p>
          </div>
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="account-nav-group">
              <span className="account-nav-group-label">{group.label}</span>
              <ul className="account-nav-list">
                {group.panes.map((pane) => (
                  <li key={pane}>
                    <button
                      type="button"
                      className={cx("account-nav-item", tab === pane && "active")}
                      aria-current={tab === pane ? "page" : undefined}
                      onClick={() => openAccount(pane)}
                    >
                      {TAB_TITLES[pane]}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <section className="account-pane">
          <header className="account-pane-head">
            <h3>{TAB_TITLES[tab]}</h3>
            <Button variant="ghost" aria-label="Close" onClick={onClose}>
              <IconClose />
            </Button>
          </header>
          <div className="account-pane-body">
            {tab === "profile" ? (
              profile.status === "failed" ? (
                <AccountFailure kind={profile.failure.kind} subject="your profile" />
              ) : me ? (
                <AccountProfileTab me={me} />
              ) : (
                <AccountLoading subject="your profile" />
              )
            ) : null}
            {tab === "usage" ? <AccountUsageTab /> : null}
            {tab === "activity" ? <AccountActivityTab /> : null}
            {tab === "storage" ? <WorkspaceStoragePanel /> : null}
            {tab === "preferences" ? <AccountPreferencesTab /> : null}
            {tab === "about" ? <AboutUnit /> : null}
          </div>
        </section>
      </div>
    </Dialog>
  );
}

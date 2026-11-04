// Update profile, remove account and connect account are INTENTIONAL placeholders: never hide them.

import type { ComponentType } from "react";
import { AboutUnit } from "./AboutUnit";
import { AccountProfileTab } from "./AccountProfileTab";
import { AccountUsageTab } from "./AccountUsageTab";
import { AccountKeysTab } from "./AccountKeysTab";
import { AccountActivityTab } from "./AccountActivityTab";
import { AccountPreferencesTab } from "./AccountPreferencesTab";
import { WorkspaceStoragePanel } from "./WorkspaceStoragePanel";
import { ReadState } from "@/components/ui";
import { cx } from "@/lib/cx";
import { meRead } from "@/lib/api";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useWorkspace } from "@/lib/workspace";

function ProfileSection() {
  return (
    <ReadState read={useRead(meRead())} subject="your profile">
      {(data) => <AccountProfileTab me={data} />}
    </ReadState>
  );
}

// The Account view's key is a section's; the server's refusals name "Account → API keys": rename them together.
const SECTIONS: readonly { key: string; label: string; group: string; Body: ComponentType }[] = [
  { key: "profile", label: "Profile", group: "Account", Body: ProfileSection },
  { key: "usage", label: "Usage & limits", group: "Account", Body: AccountUsageTab },
  { key: "keys", label: "API keys", group: "Account", Body: AccountKeysTab },
  { key: "activity", label: "Activity", group: "Account", Body: AccountActivityTab },
  { key: "preferences", label: "Preferences", group: "Account", Body: AccountPreferencesTab },
  { key: "storage", label: "Storage", group: "Workspace", Body: WorkspaceStoragePanel },
  { key: "about", label: "About this unit", group: "This unit", Body: AboutUnit },
];

const GROUPS = [...new Set(SECTIONS.map((s) => s.group))];

// A null section is the first; a key naming none leaves the nav with nothing picked.
export function AccountView({ section }: { section: string | null }) {
  const { open } = useWorkspace();
  const me = readyData(useRead(meRead()));
  const shown = SECTIONS.find((s) => s.key === (section ?? SECTIONS[0]!.key)) ?? null;
  const who = me ? (me.name ?? me.email ?? (me.provider ? me.user_id : "Local workspace")) : null;

  return (
    <section className="account-frame box-full" aria-label="Account">
      <nav className="account-nav" aria-label="Account sections">
        <div className="account-nav-head">
          <h2>Account</h2>
          <p title={who ?? undefined}>{who ?? " "}</p>
        </div>
        {GROUPS.map((group) => (
          <div key={group} className="account-nav-group">
            <span className="account-nav-group-label">{group}</span>
            <ul className="account-nav-list">
              {SECTIONS.filter((s) => s.group === group).map((s) => (
                <li key={s.key}>
                  <button
                    type="button"
                    className={cx("account-nav-item", s === shown && "active")}
                    aria-current={s === shown ? "page" : undefined}
                    onClick={() => open("account", s.key)}
                  >
                    {s.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
      <section className="account-pane">
        <header className="account-pane-head">
          <h3>{shown?.label}</h3>
        </header>
        <div className="account-pane-body">{shown && <shown.Body />}</div>
      </section>
    </section>
  );
}

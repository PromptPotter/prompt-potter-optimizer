// Update profile, remove account and connect account are INTENTIONAL placeholders: never hide them.

import type { ComponentType } from "react";
import { AccountAboutTab } from "./AccountAboutTab";
import { AccountProfileTab } from "./AccountProfileTab";
import { AccountUsageTab } from "./AccountUsageTab";
import { AccountKeysTab } from "./AccountKeysTab";
import { AccountActivityTab } from "./AccountActivityTab";
import { AccountPreferencesTab } from "./AccountPreferencesTab";
import { AccountStorageTab } from "./AccountStorageTab";
import { ToolbarFold } from "@/components/ui";
import { cx } from "@/lib/cx";
import { meRead } from "@/lib/api";
import { ACCOUNT_TAB_INFO, type AccountTab } from "@/lib/api/types";
import { wordRows } from "@/lib/api/words";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { useWorkspace } from "@/lib/state/workspace";

// Each served tab's body; its label and group are `ACCOUNT_TAB_INFO`'s, which refusals name too.
const BODIES: Record<AccountTab, ComponentType> = {
  profile: AccountProfileTab,
  usage: AccountUsageTab,
  keys: AccountKeysTab,
  activity: AccountActivityTab,
  preferences: AccountPreferencesTab,
  storage: AccountStorageTab,
  about: AccountAboutTab,
};

const TABS = wordRows(ACCOUNT_TAB_INFO).map(([key, info]) => ({
  key,
  ...info,
  Body: BODIES[key],
}));

const GROUPS = [...new Set(TABS.map((t) => t.group))];

// A null tab is the first; a key naming none leaves the nav with nothing picked.
export function AccountView({ tab }: { tab: string | null }) {
  const { open } = useWorkspace();
  const me = readyData(useRead(meRead()));
  const shown = TABS.find((t) => t.key === (tab ?? TABS[0]!.key)) ?? null;
  const who = me ? (me.name ?? me.email ?? (me.provider ? me.user_id : "Local workspace")) : null;

  return (
    <section className="account-frame box-full" aria-label="Account">
      <nav className="account-nav" aria-label="Account tabs">
        <div className="account-nav-head">
          <h2>Account</h2>
          <p title={who ?? undefined}>{who ?? " "}</p>
        </div>
        {/* A phone's one row: the open tab, the others behind ⋯. */}
        {shown && <span className="account-nav-item active account-nav-shown">{shown.label}</span>}
        <ToolbarFold
          label="Account tabs"
          actions={GROUPS.map((group) => (
            <div key={group} className="account-nav-group">
              <span className="account-nav-group-label">{group}</span>
              <ul className="account-nav-list">
                {TABS.filter((t) => t.group === group).map((t) => (
                  <li key={t.key}>
                    <button
                      type="button"
                      className={cx("account-nav-item", t === shown && "active")}
                      aria-current={t === shown ? "page" : undefined}
                      onClick={() => open("account", t.key)}
                    >
                      {t.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        />
      </nav>
      <section className="account-tab">
        <header className="account-tab-head">
          <h3>{shown?.label}</h3>
        </header>
        <div className="account-tab-body">{shown && <shown.Body />}</div>
      </section>
    </section>
  );
}

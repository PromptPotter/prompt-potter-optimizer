// The view axis, rendered only by `components/shell/ViewTabs`. A client-side closed set on
// purpose: no route names it, so the server-owns-closed-sets rule does not reach it.

export type PrimaryTab = "chat" | "dashboard";
export type RecordsTab = "measurements" | "compare" | "verify" | "files";
export type Tab = PrimaryTab | RecordsTab;

const TAB_LABEL: Record<Tab, string> = {
  chat: "Chat",
  dashboard: "Dashboard",
  measurements: "Measurements",
  compare: "Compare",
  verify: "Verify",
  files: "Files",
};

export const PRIMARY_TABS: readonly PrimaryTab[] = ["chat", "dashboard"];
export const RECORDS_TABS: readonly RecordsTab[] = ["measurements", "compare", "verify", "files"];
export const RECORDS_LABEL = "Records";
export const RECORDS_ENTRY: RecordsTab = "measurements";

export function tabLabel(tab: Tab): string {
  return TAB_LABEL[tab];
}

export function isRecordsTab(tab: Tab): tab is RecordsTab {
  return (RECORDS_TABS as readonly string[]).includes(tab);
}

export type ViewGroup = PrimaryTab | "records";

export function groupOf(tab: Tab): ViewGroup {
  return isRecordsTab(tab) ? "records" : tab;
}

// The address codec OMITS this tab, so every site must agree on what the omission restores.
export const DEFAULT_TAB: Tab = "chat";

export function isTab(s: string): s is Tab {
  return (PRIMARY_TABS as readonly string[]).includes(s) || (RECORDS_TABS as readonly string[]).includes(s);
}

// Declared here, not in AccountModal, because the address codec parses into it.
export type AccountPane =
  | "profile"
  | "usage"
  | "activity"
  | "storage"
  | "preferences"
  | "about";

export const ACCOUNT_PANES: readonly AccountPane[] = [
  "profile",
  "usage",
  "activity",
  "storage",
  "preferences",
  "about",
];

export const DEFAULT_ACCOUNT_PANE: AccountPane = "profile";

export function isAccountPane(s: string): s is AccountPane {
  return (ACCOUNT_PANES as readonly string[]).includes(s);
}

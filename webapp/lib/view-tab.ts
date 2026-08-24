// The per-campaign view axis and its HIERARCHY — one closed client union with its
// labels beside it, the same shape as `lib/run-phase.ts`. ONE surface reads it: the
// unit header's tab strip (`components/shell/ViewTabs`), at every width. Promoting a
// view out of RECORDS moves one line here and lands there.
//
// Client-side on purpose: "a closed set belongs on the server" (webapp/CLAUDE.md)
// governs shapes the API also names, and no route names this one.

export type PrimaryTab = "chat" | "dashboard";
// The run's written record — the diagnostic runs, the on-disk artifacts, and the
// cross-campaign read. Real but rare, so they ride one top-level segment together.
export type RecordsTab = "compare" | "verify" | "files";
export type Tab = PrimaryTab | RecordsTab;

const TAB_LABEL: Record<Tab, string> = {
  chat: "Chat",
  dashboard: "Dashboard",
  compare: "Compare",
  verify: "Verify",
  files: "Files",
};

export const PRIMARY_TABS: readonly PrimaryTab[] = ["chat", "dashboard"];
export const RECORDS_TABS: readonly RecordsTab[] = ["compare", "verify", "files"];
export const RECORDS_LABEL = "Records";
// Which member a click on the Records segment opens, arriving from a primary view.
// The first of the three, named rather than indexed — `RECORDS_TABS[0]` types as
// possibly-undefined and there is no honest default to fall back to.
export const RECORDS_ENTRY: RecordsTab = "compare";

export function tabLabel(tab: Tab): string {
  return TAB_LABEL[tab];
}

export function isRecordsTab(tab: Tab): tab is RecordsTab {
  return (RECORDS_TABS as readonly string[]).includes(tab);
}

// What the TOP row of the strip selects. One segment stands for the three Records
// views, so the strip's value is the group rather than the tab itself.
export type ViewGroup = PrimaryTab | "records";

export function groupOf(tab: Tab): ViewGroup {
  return isRecordsTab(tab) ? "records" : tab;
}

// The default view — what the address means when it names no tab, and where a fresh
// visitor lands. Named rather than spelled "chat" at each site, because the address
// codec OMITS it and the app must agree on what the omission restores.
export const DEFAULT_TAB: Tab = "chat";

export function isTab(s: string): s is Tab {
  return (PRIMARY_TABS as readonly string[]).includes(s) || (RECORDS_TABS as readonly string[]).includes(s);
}

// The account modal's own panes — the second view axis, and a closed client set for the
// same reason `Tab` is: no route names it. It lives here rather than inside AccountModal
// because the address codec has to name a pane, and a type declared inside the component
// that renders it cannot be the thing an address is parsed into.
export type AccountPane =
  | "profile"
  | "security"
  | "activity"
  | "storage"
  | "preferences"
  | "about";

export const ACCOUNT_PANES: readonly AccountPane[] = [
  "profile",
  "security",
  "activity",
  "storage",
  "preferences",
  "about",
];

export const DEFAULT_ACCOUNT_PANE: AccountPane = "profile";

export function isAccountPane(s: string): s is AccountPane {
  return (ACCOUNT_PANES as readonly string[]).includes(s);
}

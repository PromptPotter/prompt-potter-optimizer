// The per-campaign view axis and its HIERARCHY — one closed client union with its
// labels beside it, the same shape as `lib/run-phase.ts`. Three surfaces read it:
// the sidebar's view nav, the phone app bar's segments, and that bar's `⋯`, so
// promoting a view out of MORE moves one line here and lands in all three.
//
// Client-side on purpose: "a closed set belongs on the server" (webapp/CLAUDE.md)
// governs shapes the API also names, and no route names this one.

export type Tab = "chat" | "dashboard" | "compare" | "verify" | "files";

const TAB_LABEL: Record<Tab, string> = {
  chat: "Chat",
  dashboard: "Dashboard",
  compare: "Compare",
  verify: "Verify",
  files: "Files",
};

export const PRIMARY_TABS: readonly Tab[] = ["chat", "dashboard"];
// Real but rare — behind the sidebar's `› more` disclosure and in the phone's `⋯`.
export const MORE_TABS: readonly Tab[] = ["compare", "verify", "files"];

export function tabLabel(tab: Tab): string {
  return TAB_LABEL[tab];
}

export function isMoreTab(tab: Tab): boolean {
  return MORE_TABS.includes(tab);
}

// The default view — what the address means when it names no tab, and where a fresh
// visitor lands. Named rather than spelled "chat" at each site, because the address
// codec OMITS it and the app must agree on what the omission restores.
export const DEFAULT_TAB: Tab = "chat";

export function isTab(s: string): s is Tab {
  return (PRIMARY_TABS as readonly string[]).includes(s) || (MORE_TABS as readonly string[]).includes(s);
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

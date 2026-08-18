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

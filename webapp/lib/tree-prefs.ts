"use client";
// Per-device display preferences for the sidebar's campaign tree.
//
// Client-only and deliberately so, the same call `lib/theme.ts` makes: nothing on the server
// reads these, so making one a `UserSettings` field would buy a Pydantic model, a regeneration
// and a round-trip for a value that only decides how this browser draws. The key is named ONCE
// here because two surfaces read it — the tree that honours it and the pane that sets it.

import { useLocalStorage } from "@/lib/hooks/useLocalStorage";

const SHOW_CANDIDATES = "promptpotter.tree.showCandidates";

// A "1"/"0" codec rather than JSON, matching the sidebar's own collapse flag: the value is read
// by a human out of devtools as often as by the app.
const BOOL = { serialize: (v: boolean) => (v ? "1" : "0"), deserialize: (raw: string) => raw === "1" };

// Does a campaign row open into the candidates below it — C0, C1.1, C1.2, …?
//
// Default NO, so a campaign is the end of the sidebar tree: no ▶, nothing to expand, and the
// `/tree` read that would fill it never fires. Candidates are read on the dashboard, off the
// candidate chart, which plots them against each other — the sidebar lists them in one column
// and can only be scrolled. Turning it on is worth it for a `promptpotter-self` run, where a
// candidate CONTAINS an inner course and the nesting is the thing being read.
export function useShowCandidates(): [boolean, (next: boolean) => void] {
  return useLocalStorage<boolean>(SHOW_CANDIDATES, false, BOOL);
}

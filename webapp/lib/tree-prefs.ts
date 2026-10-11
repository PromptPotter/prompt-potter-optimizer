"use client";

import { useLocalStorage } from "@/lib/hooks/useLocalStorage";

const SHOW_CANDIDATES = "promptpotter.tree.showCandidates";

const BOOL = { serialize: (v: boolean) => (v ? "1" : "0"), deserialize: (raw: string) => raw === "1" };

// Default off, so the `/tree` read behind it never fires.
export function useShowCandidates(): [boolean, (next: boolean) => void] {
  return useLocalStorage<boolean>(SHOW_CANDIDATES, false, BOOL);
}

"use client";

import { BOOL, STORAGE_KEYS, useLocalStorage } from "@/lib/hooks/useLocalStorage";

// Default off, so the `/hierarchy/{address}` read behind it never fires.
export function useShowCandidates(): [boolean, (next: boolean) => void] {
  return useLocalStorage<boolean>(STORAGE_KEYS.treeShowCandidates, false, BOOL);
}

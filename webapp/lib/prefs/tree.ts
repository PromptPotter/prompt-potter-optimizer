import { BOOL, useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { STORAGE_KEYS } from "@/lib/storage-keys";

// Default off, so the `/hierarchy/{address}` read behind it never fires.
export function useShowCandidates(): [boolean, (next: boolean) => void] {
  return useLocalStorage<boolean>(STORAGE_KEYS.treeShowCandidates, false, BOOL);
}

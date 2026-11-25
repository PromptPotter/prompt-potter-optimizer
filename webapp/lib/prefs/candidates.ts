import { BOOL, useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { STORAGE_KEYS } from "@/lib/storage-keys";

// Default off: a run of hundreds of rounds reads one point a round (`candidateBars`).
export function useEveryCandidate(): [boolean, (next: boolean) => void] {
  return useLocalStorage<boolean>(STORAGE_KEYS.everyCandidate, false, BOOL);
}

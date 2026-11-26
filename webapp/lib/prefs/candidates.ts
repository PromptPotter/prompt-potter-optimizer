import { BOOL, useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { STORAGE_KEYS } from "@/lib/storage-keys";

// Default on: every arm a round raced keeps its bar; off, a held round reads as its leading arm
// (`candidateBars`), for a run of hundreds of rounds.
export function useEveryCandidate(): [boolean, (next: boolean) => void] {
  return useLocalStorage<boolean>(STORAGE_KEYS.everyCandidate, true, BOOL);
}

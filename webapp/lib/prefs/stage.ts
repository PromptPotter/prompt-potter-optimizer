"use client";

import { BOOL, STORAGE_KEYS, useLocalStorage } from "@/lib/hooks/useLocalStorage";

export function useStageView(): [boolean, (on: boolean) => void] {
  return useLocalStorage<boolean>(STORAGE_KEYS.stageView, false, BOOL);
}

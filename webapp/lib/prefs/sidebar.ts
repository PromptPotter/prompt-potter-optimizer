"use client";

import { BOOL, STORAGE_KEYS, useLocalStorage } from "@/lib/hooks/useLocalStorage";

export const SIDEBAR_WIDTH = { initial: 200, min: 160, max: 480 } as const;

const WIDTH = {
  serialize: String,
  deserialize: (raw: string) => {
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : SIDEBAR_WIDTH.initial;
  },
};

export function useSidebarCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useLocalStorage<boolean>(
    STORAGE_KEYS.sidebarCollapsed,
    false,
    BOOL,
  );
  return [collapsed, () => setCollapsed((prev) => !prev)];
}

export function useSidebarWidth(): [number, (next: number) => void] {
  return useLocalStorage<number>(STORAGE_KEYS.sidebarWidth, SIDEBAR_WIDTH.initial, WIDTH);
}

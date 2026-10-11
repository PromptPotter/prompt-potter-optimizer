"use client";

import { useLocalStorage } from "@/lib/hooks/useLocalStorage";

export const SIDEBAR_WIDTH = { initial: 200, min: 160, max: 480 } as const;

const BOOL = { serialize: (v: boolean) => (v ? "1" : "0"), deserialize: (raw: string) => raw === "1" };
const WIDTH = {
  serialize: String,
  deserialize: (raw: string) => {
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : SIDEBAR_WIDTH.initial;
  },
};

export function useSidebarCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useLocalStorage<boolean>(
    "promptpotter.sidebar.collapsed",
    false,
    BOOL,
  );
  return [collapsed, () => setCollapsed((prev) => !prev)];
}

export function useSidebarWidth(): [number, (next: number) => void] {
  return useLocalStorage<number>("promptpotter.sidebar.width", SIDEBAR_WIDTH.initial, WIDTH);
}

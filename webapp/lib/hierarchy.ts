import type { HierarchyTag } from "@/lib/api";

interface Tagged {
  tags: readonly HierarchyTag[];
}

export function hasTag(entry: Tagged, kind: HierarchyTag["kind"]): boolean {
  return entry.tags.some((t) => t.kind === kind);
}

export function tagValue(entry: Tagged, kind: HierarchyTag["kind"]): string | null {
  return entry.tags.find((t) => t.kind === kind)?.value ?? null;
}

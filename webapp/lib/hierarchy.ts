import type { HierarchyTag } from "@/lib/api";

interface Tagged {
  tags: readonly HierarchyTag[];
}

export function hasTag(entry: Tagged | null, kind: HierarchyTag["kind"]): boolean {
  return entry?.tags.some((t) => t.kind === kind) ?? false;
}

export function tagValue(entry: Tagged | null, kind: HierarchyTag["kind"]): string | null {
  return entry?.tags.find((t) => t.kind === kind)?.value ?? null;
}

export function tagLabel(entry: Tagged | null, kind: HierarchyTag["kind"]): string | null {
  return entry?.tags.find((t) => t.kind === kind)?.label ?? null;
}

import type { CourseNode } from "@/lib/api";
import { walkCourses } from "./lineage-candidates";

export function descendantsOf(
  root: CourseNode | null,
  seeds: Iterable<string>,
): ReadonlySet<string> {
  const out = new Set(seeds);
  if (!root || out.size === 0) return out;

  // Whole family at once: a candidate's children may sit on another course.
  const kids = new Map<string, string[]>();
  for (const course of walkCourses(root)) {
    for (const arm of course.children) {
      for (const parent of arm.parent_ids) {
        kids.set(parent, [...(kids.get(parent) ?? []), arm.id]);
      }
    }
  }

  // `out` doubles as the visited set, so a cycling tree terminates instead of hanging the tab.
  const queue = [...out];
  while (queue.length > 0) {
    const id = queue.shift() as string;
    for (const child of kids.get(id) ?? []) {
      if (out.has(child)) continue;
      out.add(child);
      queue.push(child);
    }
  }
  return out;
}

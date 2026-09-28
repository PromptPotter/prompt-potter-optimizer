// Which searchpoints stand ON a changed one, so a drawing withdraws their numbers. Walks every
// served `parent_ids` edge across forks: a change where a branch left invalidates the branch too.

import type { LineageNode } from "@/lib/api";

export function descendantsOf(
  root: LineageNode | null,
  seeds: Iterable<string>,
): ReadonlySet<string> {
  const out = new Set(seeds);
  if (!root || out.size === 0) return out;

  // Whole family at once: a candidate's children may sit on another course, so a per-course pass
  // would stop at every fork.
  const kids = new Map<string, string[]>();
  const visit = (node: LineageNode): void => {
    if (node.kind === "candidate") {
      for (const parent of node.parent_ids) {
        kids.set(parent, [...(kids.get(parent) ?? []), node.id]);
      }
    }
    for (const child of node.children) visit(child);
  };
  visit(root);

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

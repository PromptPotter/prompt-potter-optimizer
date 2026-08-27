// What stands ON a searchpoint — the mirror of the ancestor walk `compare/SearchpointPanels.tsx`
// does to draw a spine.
//
// It exists for one question: the operator changes a setting at R3, and everything the run
// measured from R3 onward stood on the value they just changed. None of it describes the run any
// more. So the drawing has to WITHDRAW those claims, and this says which ones — a pure walk over
// `parent_id`, the genealogy the server already decided. Nothing here computes a score; it decides
// which served numbers may still be shown as answers.
//
// It crosses forks, and that is the point: a fork's candidates carry the parent edge they branched
// from, so a change at the point a branch left invalidates the branch too.

import type { LineageNode } from "@/lib/api";

/** Every searchpoint descending from any of `seeds`, the seeds included. Ids, because that is what
 *  a drawing's nodes are keyed on and what `parent_id` speaks. */
export function descendantsOf(
  root: LineageNode | null,
  seeds: Iterable<string>,
): ReadonlySet<string> {
  const out = new Set(seeds);
  if (!root || out.size === 0) return out;

  // Children by parent id, over the whole family at once — a candidate's children may sit on a
  // different course than its own, so a per-course pass would stop at every fork.
  const kids = new Map<string, string[]>();
  const visit = (node: LineageNode): void => {
    if (node.kind === "candidate" && node.parent_id) {
      kids.set(node.parent_id, [...(kids.get(node.parent_id) ?? []), node.id]);
    }
    for (const child of node.children) visit(child);
  };
  visit(root);

  // Breadth-first from every seed. `out` doubles as the visited set, so a tree that somehow cycles
  // terminates instead of hanging the tab — the same guard the ancestor walk carries.
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

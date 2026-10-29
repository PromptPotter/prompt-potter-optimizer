"use client";

import type { LibrarySnippet } from "@/lib/api";
import { createModuleStore } from "@/lib/state/module-store";

// The snippet in flight between the library and a prompt field. Held here, not on the
// `DataTransfer`: a field lights while the drag passes over it, and `dragover` cannot read the payload.
const store = createModuleStore<{ dragged: LibrarySnippet | null }>({ dragged: null });

export function startSnippetDrag(snippet: LibrarySnippet): void {
  store.set({ dragged: snippet });
}

export function endSnippetDrag(): void {
  store.set({ dragged: null });
}

export function useDraggedSnippet(): LibrarySnippet | null {
  return store.useStore().dragged;
}

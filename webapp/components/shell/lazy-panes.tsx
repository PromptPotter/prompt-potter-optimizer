import { lazy, Suspense } from "react";

const loadChat = () => import("@/components/chat/ChatPane");
const loadFiles = () => import("@/components/files/FilesPane");

const LazyChat = lazy(() => loadChat().then((m) => ({ default: m.ChatPane })));
const LazyFiles = lazy(() => loadFiles().then((m) => ({ default: m.FilesPane })));

const PANE_LOADING = <div className="content" aria-busy="true" />;

export function ChatPane() {
  return (
    <Suspense fallback={PANE_LOADING}>
      <LazyChat />
    </Suspense>
  );
}

export function FilesPane() {
  return (
    <Suspense fallback={PANE_LOADING}>
      <LazyFiles />
    </Suspense>
  );
}

// A miss is swallowed: it resurfaces at the real load, which `ui/ErrorBoundary` owns.
export function preloadLazyPanes(): void {
  for (const chunk of [loadChat(), loadFiles()]) {
    void chunk.catch(() => undefined);
  }
}

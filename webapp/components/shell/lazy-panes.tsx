"use client";

import dynamic from "next/dynamic";

const PANE_LOADING = () => <div className="content" aria-busy="true" />;

export const ChatPane = dynamic(
  () => import("@/components/chat/ChatPane").then((m) => m.ChatPane),
  { ssr: false, loading: PANE_LOADING },
);
export const FilesPane = dynamic(
  () => import("@/components/files/FilesPane").then((m) => m.FilesPane),
  { ssr: false, loading: PANE_LOADING },
);
export const IngestPane = dynamic(
  () => import("@/components/ingest/IngestPane").then((m) => m.IngestPane),
  { ssr: false },
);

// A miss is swallowed: it resurfaces at the real load, which `ui/ErrorBoundary` owns.
export function preloadLazyPanes(): void {
  for (const chunk of [
    import("@/components/chat/ChatPane"),
    import("@/components/files/FilesPane"),
  ]) {
    void chunk.catch(() => undefined);
  }
}

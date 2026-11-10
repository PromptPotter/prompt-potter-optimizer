import { lazy, Suspense, type ComponentType } from "react";

// Every surface that draws a chart, and the chat, files, compare and account panes, loads on first
// use: chart.js stays out of the first-paint chunk.
const loadChat = () => import("@/components/chat/ChatPane");
const loadFiles = () => import("@/components/files/FilesPane");
const loadCompare = () => import("@/components/compare/ComparePane");
const loadAccount = () => import("@/components/account/AccountView");
const loadCandidates = () => import("@/components/candidates/CandidatesCard");
const loadSpend = () => import("@/components/dashboard/scoring/CostStrip");

const PANE_LOADING = <div className="content" aria-busy="true" />;

function suspended<P extends object>(Loaded: ComponentType<P>) {
  return function Suspended(props: P) {
    return (
      <Suspense fallback={PANE_LOADING}>
        <Loaded {...props} />
      </Suspense>
    );
  };
}

export const ChatPane = suspended(lazy(() => loadChat().then((m) => ({ default: m.ChatPane }))));
export const FilesPane = suspended(lazy(() => loadFiles().then((m) => ({ default: m.FilesPane }))));
export const ComparePane = suspended(lazy(() => loadCompare().then((m) => ({ default: m.ComparePane }))));
export const AccountView = suspended(lazy(() => loadAccount().then((m) => ({ default: m.AccountView }))));
export const CandidatesCard = suspended(
  lazy(() => loadCandidates().then((m) => ({ default: m.CandidatesCard }))),
);
export const CostStrip = suspended(lazy(() => loadSpend().then((m) => ({ default: m.CostStrip }))));

// A miss is swallowed: it resurfaces at the real load, which `ui/ErrorBoundary` owns.
export function preloadLazyPanes(): void {
  for (const chunk of [
    loadChat(),
    loadFiles(),
    loadCompare(),
    loadAccount(),
    loadCandidates(),
    loadSpend(),
  ]) {
    void chunk.catch(() => undefined);
  }
}

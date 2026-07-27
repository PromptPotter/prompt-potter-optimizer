import { AppShell } from "@/components/shell/AppShell";
import { ConsentGate } from "@/components/onboarding/ConsentGate";
import { ErrorBoundary } from "@/components/ui";
import { ViewMemoryProvider } from "@/lib/view-memory";
import { WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  // ErrorBoundary wraps the provider so a crash inside WorkspaceProvider
  // itself is still caught.
  return (
    <ErrorBoundary>
      <WorkspaceProvider>
        {/* Per-campaign view memory. Above AppShell so the shell itself can read it —
            the sidebar's expand/collapse and the "put me back where I was" restore are
            both AppShell-level concerns. It stores only ids and UI keys, so it depends
            on neither selection nor the served tree. */}
        <ViewMemoryProvider>
          <AppShell />
        </ViewMemoryProvider>
        {/* Blocking overlay for a signed-in user who hasn't accepted the
            current Terms — self-hides for anon (read-only preview) and the
            already-consented. */}
        <ConsentGate />
      </WorkspaceProvider>
    </ErrorBoundary>
  );
}

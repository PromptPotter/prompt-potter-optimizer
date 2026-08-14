import { AccessGate } from "@/components/onboarding/AccessGate";
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
        {/* Two blocking overlays, mutually exclusive by construction. AccessGate
            holds an account the operator has blocked; ConsentGate holds an entitled
            one that hasn't accepted the current Terms. Both self-hide for anon
            (read-only preview). */}
        <AccessGate />
        <ConsentGate />
      </WorkspaceProvider>
    </ErrorBoundary>
  );
}

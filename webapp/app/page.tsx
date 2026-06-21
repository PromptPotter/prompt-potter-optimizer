import { AppShell } from "@/components/shell/AppShell";
import { ConsentGate } from "@/components/onboarding/ConsentGate";
import { ErrorBoundary } from "@/components/ui";
import { WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  // ErrorBoundary wraps the provider so a crash inside WorkspaceProvider
  // itself is still caught.
  return (
    <ErrorBoundary>
      <WorkspaceProvider>
        <AppShell />
        {/* Blocking overlay for a signed-in user who hasn't accepted the
            current Terms — self-hides for anon (read-only preview) and the
            already-consented. */}
        <ConsentGate />
      </WorkspaceProvider>
    </ErrorBoundary>
  );
}

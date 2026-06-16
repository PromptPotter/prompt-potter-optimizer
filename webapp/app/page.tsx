import { AppShell } from "@/components/shell/AppShell";
import { ErrorBoundary } from "@/components/ui";
import { WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  // ErrorBoundary wraps the provider so a crash inside WorkspaceProvider
  // itself is still caught.
  return (
    <ErrorBoundary>
      <WorkspaceProvider>
        <AppShell />
      </WorkspaceProvider>
    </ErrorBoundary>
  );
}

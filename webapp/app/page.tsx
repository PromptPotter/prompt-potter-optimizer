import { DashboardPane } from "@/components/dashboard/DashboardPane";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  // ErrorBoundary wraps the provider so a crash inside WorkspaceProvider
  // itself is still caught.
  return (
    <ErrorBoundary>
      <WorkspaceProvider>
        <DashboardPane />
      </WorkspaceProvider>
    </ErrorBoundary>
  );
}

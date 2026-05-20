import { DashboardPane } from "@/components/dashboard/DashboardPane";
import { WorkspaceProvider } from "@/lib/workspace";

export default function Home() {
  return (
    <WorkspaceProvider>
      <DashboardPane />
    </WorkspaceProvider>
  );
}

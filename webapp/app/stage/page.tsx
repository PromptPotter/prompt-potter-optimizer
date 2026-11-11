import { Stage } from "@/components/stage/Stage";
import { ErrorBoundary } from "@/components/ui";
import { ViewMemoryProvider } from "@/lib/state/view-memory";
import { WorkspaceProvider } from "@/lib/workspace";
import { mount } from "../mount";

mount(
  <ErrorBoundary>
    <ViewMemoryProvider>
      <WorkspaceProvider>
        <Stage />
      </WorkspaceProvider>
    </ViewMemoryProvider>
  </ErrorBoundary>,
);

import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/index.css";
import { AuthProvider } from "@/lib/state/auth";
import { SurfaceFavicon } from "@/components/brand/SurfaceFavicon";

// Every page's one root: its `index.html` holds `#root` and the head `vite.config.ts` writes.
export function mount(page: ReactNode): void {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <SurfaceFavicon />
      <AuthProvider>{page}</AuthProvider>
    </StrictMode>,
  );
}

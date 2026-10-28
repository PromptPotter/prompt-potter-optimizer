"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";
import { buildIsStale } from "@/lib/api";
import s from "./ErrorBoundary.module.css";

interface Props {
  children: ReactNode;
}

type ReloadOutcome = "reloading" | "already-tried" | "cannot-track";

interface State {
  error: Error | null;
  reload: ReloadOutcome | null;
}

const MISSING_CHUNK = /ChunkLoadError|Failed to load chunk|Loading chunk \S+ failed|dynamically imported module|Importing a module script failed/i;

// Two faces of one cause: a piece of the old build is gone from disk, or the old build read an answer the new API shaped.
function fromStaleBuild(error: Error): boolean {
  return buildIsStale() || MISSING_CHUNK.test(`${error.name} ${error.message}`);
}

// Reload ONCE. A time, not a flag, so a later rebuild heals too without anyone clearing it.
const RELOAD_STAMP = "promptpotter.chunk-reload-at";
const RELOAD_GUARD_MS = 20_000;

function reloadOnceForStaleBuild(): ReloadOutcome {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_STAMP) ?? 0);
    if (Number.isFinite(last) && Date.now() - last < RELOAD_GUARD_MS) return "already-tried";
    sessionStorage.setItem(RELOAD_STAMP, String(Date.now()));
  } catch {
    // Storage blocked: the stamp is the only bound, so reloading here would loop. Ask instead.
    return "cannot-track";
  }
  window.location.reload();
  return "reloading";
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null, reload: null };

  static getDerivedStateFromError(error: Error): State {
    return { error, reload: null };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[dashboard] render error", error, info.componentStack);
    if (fromStaleBuild(error)) {
      this.setState({ reload: reloadOnceForStaleBuild() });
    }
  }

  override render(): ReactNode {
    const { error, reload } = this.state;
    if (!error) return this.props.children;
    const stale = fromStaleBuild(error);
    return (
      <div role="alert" className={s.panel}>
        <h1 className={s.heading}>
          {stale ? "This tab is running an old build" : "The dashboard hit a render error"}
        </h1>
        <p className={s.body}>
          {!stale
            ? "A render error never writes to disk — your campaign is untouched. Reloading usually clears it; if it repeats, the console has the component stack."
            : reload === "already-tried"
              ? "Your campaign is untouched — the app was rebuilt while this tab was open, so this tab no longer matches it. Reloading was tried once already and did not clear it, which usually means a build is still running; wait for it to finish, then reload."
              : "Your campaign is untouched — the app was rebuilt while this tab was open, so this tab no longer matches it. Reload once the build has finished."}
        </p>
        <pre className={s.detail}>{error.message}</pre>
        <button type="button" className={s.action} onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}

"use client";
// Root render-error boundary; render-path errors only — async failures land in component state
// through `useRead` / `useCommand`.

import { Component, type ErrorInfo, type ReactNode } from "react";
import s from "./ErrorBoundary.module.css";

interface Props {
  children: ReactNode;
}

type ReloadOutcome = "reloading" | "already-tried" | "cannot-track";

interface State {
  error: Error | null;
  reload: ReloadOutcome | null;
}

// A chunk 404 is this tab holding the previous build's manifest: `out/` is served off disk, so a
// rebuild swaps every chunk hash under every open tab.
const STALE_BUILD = /ChunkLoadError|Failed to load chunk|Loading chunk \S+ failed|dynamically imported module|Importing a module script failed/i;

// Reload ONCE. A time, not a flag, so a later rebuild heals too without anyone clearing it.
const RELOAD_STAMP = "pp:chunk-reload-at";
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
  state: State = { error: null, reload: null };

  static getDerivedStateFromError(error: Error): State {
    return { error, reload: null };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[dashboard] render error", error, info.componentStack);
    if (STALE_BUILD.test(`${error.name} ${error.message}`)) {
      this.setState({ reload: reloadOnceForStaleBuild() });
    }
  }

  render(): ReactNode {
    const { error, reload } = this.state;
    if (!error) return this.props.children;
    const stale = STALE_BUILD.test(`${error.name} ${error.message}`);
    return (
      <div role="alert" className={s.panel}>
        <h1 className={s.heading}>
          {stale ? "This tab is running an old build" : "The dashboard hit a render error"}
        </h1>
        <p className={s.body}>
          {!stale
            ? "A render error never writes to disk — your campaign is untouched. Reloading usually clears it; if it repeats, the console has the component stack."
            : reload === "already-tried"
              ? "Your campaign is untouched — the app was rebuilt while this tab was open, so a piece of it is no longer on disk. Reloading was tried once already and the file is still missing, which usually means a build is still running; wait for it to finish, then reload."
              : "Your campaign is untouched — the app was rebuilt while this tab was open, so a piece of it is no longer on disk. Reload once the build has finished."}
        </p>
        <pre className={s.detail}>{error.message}</pre>
        <button type="button" className={s.action} onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}

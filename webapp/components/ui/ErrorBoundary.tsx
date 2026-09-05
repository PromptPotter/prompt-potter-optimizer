"use client";
// Root render-error boundary. A malformed payload that throws while a panel
// drills into it would otherwise white-screen the whole dashboard; this
// catches it and shows a recoverable fallback instead. Boundaries catch
// render-path errors only — async failures in fetch / interval callbacks
// are already funnelled into component state by the poll loops.
//
// Inline styles throughout so the fallback renders even if the stylesheet
// itself failed to load.

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

// What the auto-reload did about a stale chunk, so the fallback can say the true one. It used to
// say "reloading was tried once already" unconditionally, which is a guess on two of the three
// arms — and on `cannot-track` it was the opposite of what happened.
type ReloadOutcome = "reloading" | "already-tried" | "cannot-track";

interface State {
  error: Error | null;
  reload: ReloadOutcome | null;
}

// A chunk that 404s is not a render bug — it is THIS tab holding the previous
// build's manifest. `out/` is served straight off disk (`main.py::StaticFiles`),
// so any rebuild swaps every chunk hash under every open tab, and the miss
// surfaces only when a lazy route asks for one. Matched on the error rather
// than the status because a boundary never sees the response.
const STALE_BUILD = /ChunkLoadError|Failed to load chunk|Loading chunk \S+ failed|dynamically imported module|Importing a module script failed/i;

// Reloading is the fix, so do it — ONCE. The stamp is a time, not a flag: a
// boolean would need clearing on every good render to let a later rebuild heal
// itself too, and whoever forgot that would leave the tab stuck for the session.
// A second failure inside the window means the chunk is genuinely gone, and
// then the operator sees it rather than a reload loop.
const RELOAD_STAMP = "pp:chunk-reload-at";
const RELOAD_GUARD_MS = 20_000;

function reloadOnceForStaleBuild(): ReloadOutcome {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_STAMP) ?? 0);
    if (Number.isFinite(last) && Date.now() - last < RELOAD_GUARD_MS) return "already-tried";
    sessionStorage.setItem(RELOAD_STAMP, String(Date.now()));
  } catch {
    // Storage blocked (private mode, site-data off, partitioned storage). The stamp is the ONLY
    // thing bounding this, so without it a reload is not "once" — the chunk is still missing on
    // the next pass, which throws, which reloads, for as long as the build takes. Reload nothing
    // and let the fallback ask; the operator's own reload is bounded by the operator.
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
    // The operator's only signal in a static export — surface it to the
    // console with the component stack.
    console.error("[dashboard] render error", error, info.componentStack);
    if (STALE_BUILD.test(`${error.name} ${error.message}`)) {
      this.setState({ reload: reloadOnceForStaleBuild() });
    }
  }

  render(): ReactNode {
    const { error, reload } = this.state;
    if (!error) return this.props.children;
    // Reaching here on a stale build means the chunk is not on disk, so say THAT — "render error"
    // sends the operator hunting a component stack for a file that simply is not there.
    const stale = STALE_BUILD.test(`${error.name} ${error.message}`);
    return (
      <div
        role="alert"
        style={{
          maxWidth: 560,
          margin: "10vh auto",
          padding: 24,
          fontFamily: "system-ui, sans-serif",
          color: "#e6e6e6",
          background: "#1a1a1e",
          border: "1px solid #3a3a42",
          borderRadius: 10,
        }}
      >
        <h1 style={{ fontSize: "var(--text-2xl)", margin: "0 0 8px" }}>
          {stale ? "This tab is running an old build" : "The dashboard hit a render error"}
        </h1>
        <p style={{ fontSize: "var(--text-md)", lineHeight: 1.5, margin: "0 0 12px" }}>
          {!stale
            ? "A render error never writes to disk — your campaign is untouched. Reloading usually clears it; if it repeats, the console has the component stack."
            : reload === "already-tried"
              ? "Your campaign is untouched — the app was rebuilt while this tab was open, so a piece of it is no longer on disk. Reloading was tried once already and the file is still missing, which usually means a build is still running; wait for it to finish, then reload."
              : "Your campaign is untouched — the app was rebuilt while this tab was open, so a piece of it is no longer on disk. Reload once the build has finished."}
        </p>
        <pre
          style={{
            fontSize: "var(--text-sm)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: "#ff8a8a",
            margin: "0 0 16px",
          }}
        >
          {error.message}
        </pre>
        <button
          type="button"
          onClick={() => window.location.reload()}
          style={{
            fontSize: "var(--text-md)",
            padding: "8px 16px",
            color: "#fff",
            background: "#3b6fe0",
            border: "none",
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          Reload
        </button>
      </div>
    );
  }
}

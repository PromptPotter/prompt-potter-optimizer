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

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The operator's only signal in a static export — surface it to the
    // console with the component stack.
    console.error("[dashboard] render error", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
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
          The dashboard hit a render error
        </h1>
        <p style={{ fontSize: "var(--text-md)", lineHeight: 1.5, margin: "0 0 12px" }}>
          This is a read-only view — your campaign on disk is untouched.
          Reloading usually clears it; if it repeats, the console has the
          component stack.
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

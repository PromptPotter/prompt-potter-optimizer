"use client";
import { useEffect, useState } from "react";
import { marked } from "marked";
import { fetchCycleFile } from "@/lib/api";

interface Props {
  cycleId: string | null;
  selected: { scope: string; path: string } | null;
}

interface ViewerState {
  meta: string;
  body: string;
  contentType: string;
  isMarkdown: boolean;
  error: string | null;
}

const EMPTY: ViewerState = {
  meta: "",
  body: "Select a file from the tree to preview its content. JSON renders with formatting; .md renders as Markdown; .log renders as plain text.",
  contentType: "",
  isMarkdown: false,
  error: null,
};

export function FileViewer({ cycleId, selected }: Props) {
  const [state, setState] = useState<ViewerState>(EMPTY);

  useEffect(() => {
    if (!cycleId || !selected) {
      setState(EMPTY);
      return;
    }
    let cancelled = false;
    setState({ meta: "loading file…", body: "", contentType: "", isMarkdown: false, error: null });
    (async () => {
      try {
        const r = (await fetchCycleFile(cycleId, selected.scope, selected.path)) as {
          path: string; scope: string; content: string | null; size?: number; content_type?: string;
        };
        if (cancelled) return;
        const ct = r.content_type ?? "";
        const meta = `${r.size ?? "?"} B • ${ct || "text"}`;
        if (r.content == null) {
          setState({
            meta,
            body: (r.size ?? 0) > 2 * 1024 * 1024
              ? "(preview truncated — file > 2 MiB)"
              : "(preview unavailable — binary)",
            contentType: ct,
            isMarkdown: false,
            error: null,
          });
          return;
        }
        if (ct === "json") {
          let body = r.content;
          try { body = JSON.stringify(JSON.parse(r.content), null, 2); } catch { /* keep raw */ }
          setState({ meta, body, contentType: ct, isMarkdown: false, error: null });
        } else if (ct === "markdown") {
          const html = await Promise.resolve(marked.parse(r.content));
          setState({ meta, body: typeof html === "string" ? html : r.content, contentType: ct, isMarkdown: true, error: null });
        } else {
          setState({ meta, body: r.content, contentType: ct, isMarkdown: false, error: null });
        }
      } catch (e) {
        if (cancelled) return;
        setState({
          meta: "",
          body: `Failed to load: ${(e as Error).message}`,
          contentType: "",
          isMarkdown: false,
          error: (e as Error).message,
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cycleId, selected]);

  const headerPath = selected ? `${selected.scope}: ${selected.path}` : "(no file selected)";
  return (
    <div className="viewer-pane">
      <div className="viewer-header">
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {headerPath}
        </span>
        <span style={{ color: "var(--color-text-tertiary)" }}>{state.meta}</span>
      </div>
      {state.isMarkdown ? (
        <div className="viewer-body markdown" dangerouslySetInnerHTML={{ __html: state.body }} />
      ) : (
        <div className={`viewer-body${selected ? "" : " empty"}`}>{state.body}</div>
      )}
    </div>
  );
}

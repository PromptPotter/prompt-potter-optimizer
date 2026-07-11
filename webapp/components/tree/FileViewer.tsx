"use client";
import { marked } from "marked";
import { fetchCycleFile } from "@/lib/api";
import { useFetch } from "@/lib/hooks/useFetch";
import { RoundFileView, type RoundDoc } from "./RoundFileView";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  selected: { scope: string; path: string } | null;
}

interface ViewerState {
  meta: string;
  body: string;
  contentType: string;
  isMarkdown: boolean;
  roundDoc: RoundDoc | null;
  rawJson: string;
  error: string | null;
}

const EMPTY: ViewerState = {
  meta: "",
  body: "Select a file from the tree to preview its content. JSON renders with formatting; .md renders as Markdown; .log renders as plain text. Round files (rounds/round_NNNN.json) render as a scoreboard + per-sample table.",
  contentType: "",
  isMarkdown: false,
  roundDoc: null,
  rawJson: "",
  error: null,
};

const LOADING: ViewerState = {
  meta: "loading file…",
  body: "",
  contentType: "",
  isMarkdown: false,
  roundDoc: null,
  rawJson: "",
  error: null,
};

const ROUND_FILE_RE = /^rounds\/round_\d+\.json$/;

function isRoundFile(selected: { scope: string; path: string } | null): boolean {
  return !!selected && selected.scope === "cycle" && ROUND_FILE_RE.test(selected.path);
}

export function FileViewer({ campaignId, cycleId, selected }: Props) {
  // Bundle the non-null trio so the fetcher closure inherits the narrowing.
  const ready = campaignId && cycleId && selected ? { campaignId, cycleId, selected } : null;

  // useFetch owns cancellation + the key-scoped reset (data blanks to null on a
  // deps change, in-render) — so this component no longer hand-rolls either.
  const { data, error } = useFetch<ViewerState>(
    ready
      ? async (): Promise<ViewerState> => {
          const r = await fetchCycleFile(
            ready.campaignId,
            ready.cycleId,
            ready.selected.scope,
            ready.selected.path,
          );
          const ct = r.content_type;
          const meta = `${r.size} B • ${ct}`;
          if (r.content == null) {
            return {
              meta,
              body:
                r.size > 2 * 1024 * 1024
                  ? "(preview truncated — file > 2 MiB)"
                  : "(preview unavailable — binary)",
              contentType: ct,
              isMarkdown: false,
              roundDoc: null,
              rawJson: "",
              error: null,
            };
          }
          if (ct === "json") {
            let body = r.content;
            let parsed: unknown = null;
            try {
              parsed = JSON.parse(r.content);
              body = JSON.stringify(parsed, null, 2);
            } catch {
              /* keep raw */
            }
            const roundDoc =
              isRoundFile(ready.selected) && parsed && typeof parsed === "object"
                ? (parsed as RoundDoc)
                : null;
            return { meta, body, contentType: ct, isMarkdown: false, roundDoc, rawJson: body, error: null };
          }
          if (ct === "markdown") {
            const html = await Promise.resolve(marked.parse(r.content));
            return {
              meta,
              body: typeof html === "string" ? html : r.content,
              contentType: ct,
              isMarkdown: true,
              roundDoc: null,
              rawJson: "",
              error: null,
            };
          }
          return { meta, body: r.content, contentType: ct, isMarkdown: false, roundDoc: null, rawJson: "", error: null };
        }
      : null,
    [campaignId, cycleId, selected?.scope ?? "", selected?.path ?? ""],
  );

  const state: ViewerState = !ready
    ? EMPTY
    : error
      ? { ...EMPTY, meta: "", body: `Failed to load: ${error}`, error }
      : (data ?? LOADING);

  const headerPath = selected ? `${selected.scope}: ${selected.path}` : "(no file selected)";
  return (
    <div className="viewer-pane">
      <div className="viewer-header">
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {headerPath}
        </span>
        <span style={{ color: "var(--color-text-tertiary)" }}>{state.meta}</span>
      </div>
      {state.roundDoc ? (
        <div className="viewer-body viewer-structured">
          <RoundFileView doc={state.roundDoc} raw={state.rawJson} />
        </div>
      ) : state.isMarkdown ? (
        <div className="viewer-body markdown" dangerouslySetInnerHTML={{ __html: state.body }} />
      ) : (
        <div className={`viewer-body${selected ? "" : " empty"}`}>{state.body}</div>
      )}
    </div>
  );
}

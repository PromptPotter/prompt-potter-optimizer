"use client";
import { useMemo } from "react";
import { renderMarkdownSafe } from "@/lib/markdown";
import { cycleFileRead, type FileContentResponse } from "@/lib/api";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { RoundFileView } from "./RoundFileView";

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
}

const EMPTY: ViewerState = {
  meta: "",
  body: "Select a file from the tree to preview its content. JSON renders with formatting; .md renders as Markdown; .log renders as plain text. Round files (rounds/round_NNNN.json) render as their served round: a scoreboard + its measured cells.",
  contentType: "",
  isMarkdown: false,
};

const LOADING: ViewerState = { meta: "loading file…", body: "", contentType: "", isMarkdown: false };

const ROUND_FILE_RE = /^rounds\/round_(\d+)\.json$/;

function roundOfFile(selected: { scope: string; path: string } | null): number | null {
  const n = selected?.scope === "cycle" ? ROUND_FILE_RE.exec(selected.path)?.[1] : undefined;
  return n === undefined ? null : Number(n);
}

function viewerState(r: FileContentResponse): ViewerState {
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
    };
  }
  if (ct === "json") {
    let body = r.content;
    try {
      body = JSON.stringify(JSON.parse(r.content), null, 2);
    } catch {
      /* keep raw */
    }
    return { meta, body, contentType: ct, isMarkdown: false };
  }
  if (ct === "markdown") {
    // `renderMarkdownSafe`, never `marked.parse`: tenant-supplied text reaches `dangerouslySetInnerHTML`.
    return { meta, body: renderMarkdownSafe(r.content), contentType: ct, isMarkdown: true };
  }
  return { meta, body: r.content, contentType: ct, isMarkdown: false };
}

export function FileViewer({ campaignId, cycleId, selected }: Props) {
  const ready = campaignId && cycleId && selected ? { campaignId, cycleId, selected } : null;

  const read = useRead(
    ready
      ? cycleFileRead(
          [{ campaignId: ready.campaignId, cycleId: ready.cycleId }],
          ready.selected.scope,
          ready.selected.path,
        )
      : null,
  );
  const file = readyData(read);
  const shown = useMemo(() => (file ? viewerState(file) : null), [file]);
  const round = ready ? roundOfFile(ready.selected) : null;

  const state: ViewerState = !ready
    ? EMPTY
    : read.status === "failed"
      ? { ...EMPTY, meta: "", body: "Could not load this file." }
      : (shown ?? LOADING);

  const headerPath = selected ? `${selected.scope}: ${selected.path}` : "(no file selected)";
  return (
    <div className="viewer-pane">
      <div className="viewer-header">
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {headerPath}
        </span>
        <span style={{ color: "var(--color-text-secondary)" }}>{state.meta}</span>
      </div>
      {ready && round !== null && shown ? (
        <div className="viewer-body viewer-structured">
          <RoundFileView
            path={[{ campaignId: ready.campaignId, cycleId: ready.cycleId }]}
            round={round}
            raw={shown.body}
          />
        </div>
      ) : state.isMarkdown ? (
        <div className="viewer-body markdown" dangerouslySetInnerHTML={{ __html: state.body }} />
      ) : (
        <div className={`viewer-body${selected ? "" : " empty"}`}>{state.body}</div>
      )}
    </div>
  );
}

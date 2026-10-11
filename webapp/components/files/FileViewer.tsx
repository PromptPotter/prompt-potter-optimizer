"use client";
import { useMemo } from "react";
import { renderMarkdownSafe } from "@/lib/markdown";
import { cycleFileRead, type FileContentResponse } from "@/lib/api";
import type { RoundResult } from "@/lib/api/types";
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
  roundDoc: RoundResult | null;
  rawJson: string;
}

const EMPTY: ViewerState = {
  meta: "",
  body: "Select a file from the tree to preview its content. JSON renders with formatting; .md renders as Markdown; .log renders as plain text. Round files (rounds/round_NNNN.json) render as a scoreboard + per-sample table.",
  contentType: "",
  isMarkdown: false,
  roundDoc: null,
  rawJson: "",
};

const LOADING: ViewerState = {
  meta: "loading file…",
  body: "",
  contentType: "",
  isMarkdown: false,
  roundDoc: null,
  rawJson: "",
};

const ROUND_FILE_RE = /^rounds\/round_\d+\.json$/;

function isRoundFile(selected: { scope: string; path: string } | null): boolean {
  return !!selected && selected.scope === "cycle" && ROUND_FILE_RE.test(selected.path);
}

function viewerState(
  r: FileContentResponse,
  selected: { scope: string; path: string },
): ViewerState {
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
      isRoundFile(selected) && parsed && typeof parsed === "object"
        ? (parsed as RoundResult)
        : null;
    return { meta, body, contentType: ct, isMarkdown: false, roundDoc, rawJson: body };
  }
  if (ct === "markdown") {
    // `renderMarkdownSafe`, never `marked.parse`: tenant-supplied text reaches `dangerouslySetInnerHTML`.
    return {
      meta,
      body: renderMarkdownSafe(r.content),
      contentType: ct,
      isMarkdown: true,
      roundDoc: null,
      rawJson: "",
    };
  }
  return { meta, body: r.content, contentType: ct, isMarkdown: false, roundDoc: null, rawJson: "" };
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
  const scope = selected?.scope ?? null;
  const path = selected?.path ?? null;
  const shown = useMemo(
    () => (file && scope !== null && path !== null ? viewerState(file, { scope, path }) : null),
    [file, scope, path],
  );

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

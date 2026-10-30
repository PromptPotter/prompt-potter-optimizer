"use client";
import { useMemo } from "react";
import { renderMarkdownSafe } from "@/lib/markdown";
import { cycleFileRead, type FileContentResponse } from "@/lib/api";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { ReadState } from "@/components/ui";
import { RoundFileView } from "./RoundFileView";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  selected: { scope: string; path: string } | null;
}

interface ViewerState {
  body: string;
  isMarkdown: boolean;
}

const NO_FILE =
  "Select a file from the tree to preview its content. JSON renders with formatting; .md renders as Markdown; .log renders as plain text. Round files (rounds/round_NNNN.json) render as their served round: a scoreboard + its measured cells.";

const ROUND_FILE_RE = /^rounds\/round_(\d+)\.json$/;

function roundOfFile(selected: { scope: string; path: string } | null): number | null {
  const n = selected?.scope === "cycle" ? ROUND_FILE_RE.exec(selected.path)?.[1] : undefined;
  return n === undefined ? null : Number(n);
}

function viewerState(r: FileContentResponse): ViewerState {
  if (r.content == null) {
    return {
      body:
        r.size > 2 * 1024 * 1024
          ? "(preview truncated — file > 2 MiB)"
          : "(preview unavailable — binary)",
      isMarkdown: false,
    };
  }
  if (r.content_type === "json") {
    let body = r.content;
    try {
      body = JSON.stringify(JSON.parse(r.content), null, 2);
    } catch {
      /* keep raw */
    }
    return { body, isMarkdown: false };
  }
  if (r.content_type === "markdown") {
    // `renderMarkdownSafe`, never `marked.parse`: tenant-supplied text reaches `dangerouslySetInnerHTML`.
    return { body: renderMarkdownSafe(r.content), isMarkdown: true };
  }
  return { body: r.content, isMarkdown: false };
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
  const file = shownData(read);
  const round = ready ? roundOfFile(ready.selected) : null;

  const headerPath = selected ? `${selected.scope}: ${selected.path}` : "(no file selected)";
  return (
    <div className="viewer-pane">
      <div className="viewer-header">
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {headerPath}
        </span>
        <span style={{ color: "var(--color-text-secondary)" }}>
          {file ? `${file.size} B • ${file.content_type}` : ""}
        </span>
      </div>
      <ReadState
        read={read}
        subject="this file"
        className="viewer-body"
        idle={<div className="viewer-body empty">{NO_FILE}</div>}
      >
        {(f) => <FileBody file={f} round={ready && round !== null ? { at: ready, n: round } : null} />}
      </ReadState>
    </div>
  );
}

function FileBody({
  file,
  round,
}: {
  file: FileContentResponse;
  round: { at: { campaignId: string; cycleId: string }; n: number } | null;
}) {
  const shown = useMemo(() => viewerState(file), [file]);
  if (round !== null) {
    return (
      <div className="viewer-body viewer-structured">
        <RoundFileView
          path={[{ campaignId: round.at.campaignId, cycleId: round.at.cycleId }]}
          round={round.n}
          raw={shown.body}
        />
      </div>
    );
  }
  return shown.isMarkdown ? (
    <div className="viewer-body markdown" dangerouslySetInnerHTML={{ __html: shown.body }} />
  ) : (
    <div className="viewer-body">{shown.body}</div>
  );
}

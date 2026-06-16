"use client";

import { useRef, useState } from "react";
import type { PipelineDependencyWire } from "@/lib/api";
import { cx } from "@/lib/cx";

// The active pipeline's required inputs — derived server-side from its node types
// (a `candidate_source` node needs a target library). Each unfulfilled dependency
// offers two unified ways to fulfil it in place: drop a file, or build it from one
// of the dataset's own columns (the target column / the union of its category
// sheets). Fulfilled ones collapse to a confirmation line. The library is a soft
// dependency — it sharpens the candidate pool but doesn't gate Start (the answers
// already in the data are a runnable pool), so this never blocks the campaign.
export function PipelineDependencies({
  dependencies,
  librarySize,
  headers,
  targetColumn,
  onUpload,
  onBuildFromColumn,
  busy,
}: {
  dependencies: PipelineDependencyWire[];
  librarySize: number;
  headers: string[];
  targetColumn: string;
  onUpload: (file: File) => void;
  onBuildFromColumn: (column: string) => void;
  busy: boolean;
}) {
  if (dependencies.length === 0) return null;
  return (
    <div className="ingest-dependencies" role="group" aria-label="Pipeline inputs">
      {dependencies.map((dep) => (
        <DependencyRow
          key={`${dep.kind}:${dep.node}`}
          dep={dep}
          librarySize={librarySize}
          headers={headers}
          targetColumn={targetColumn}
          onUpload={onUpload}
          onBuildFromColumn={onBuildFromColumn}
          busy={busy}
        />
      ))}
    </div>
  );
}

function DependencyRow({
  dep,
  librarySize,
  headers,
  targetColumn,
  onUpload,
  onBuildFromColumn,
  busy,
}: {
  dep: PipelineDependencyWire;
  librarySize: number;
  headers: string[];
  targetColumn: string;
  onUpload: (file: File) => void;
  onBuildFromColumn: (column: string) => void;
  busy: boolean;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Default the build-from column to the resolved target column when it's a real
  // header, else the first column.
  const [column, setColumn] = useState(
    headers.includes(targetColumn) ? targetColumn : (headers[0] ?? ""),
  );
  return (
    <div className={cx("ingest-dependency", dep.fulfilled && "is-fulfilled")}>
      <div className="ingest-dependency-head">
        <span className="ingest-dependency-title">{dep.title}</span>
        <span
          className={cx("ingest-dependency-status", dep.fulfilled ? "ok" : "missing")}
          aria-label={dep.fulfilled ? "Fulfilled" : "Missing"}
        >
          {dep.fulfilled ? `✓ ${librarySize} targets` : "Missing"}
        </span>
      </div>
      <p className="ingest-dependency-hint">{dep.hint}</p>
      <div className="ingest-dependency-actions">
        {/* Build from the dataset's own columns — the unified, no-file path. */}
        {headers.length > 0 ? (
          <div className="ingest-dependency-build">
            <label className="ingest-dependency-build-label">
              Build from column
              <select
                className="ingest-dependency-select"
                value={column}
                disabled={busy}
                onChange={(e) => setColumn(e.target.value)}
                aria-label="Dataset column to build the library from"
              >
                {headers.map((h) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="chat-cta-btn"
              disabled={busy || !column}
              onClick={() => onBuildFromColumn(column)}
            >
              Build from dataset
            </button>
          </div>
        ) : null}
        {/* Or drop an external list. */}
        <input
          ref={inputRef}
          type="file"
          hidden
          accept=".txt,.csv,.tsv,.json,.jsonl,.ndjson,.xlsx"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onUpload(f);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className="chat-cta-btn secondary"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          {dep.fulfilled ? "Replace with a file" : "or drop a file"}
        </button>
      </div>
    </div>
  );
}

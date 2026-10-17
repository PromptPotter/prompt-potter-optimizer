"use client";

import { useRef, useState } from "react";
import type { DraftDependency } from "@/lib/api";

interface DependencyProps {
  librarySize: number;
  headers: string[];
  targetColumn: string;
  onUpload: (file: File) => void;
  onBuildFromColumn: (column: string) => void;
  busy: boolean;
}

// Soft: the answers already in the data are a runnable pool, so it never gates Start.
export function PipelineDependencies({
  dependencies,
  ...props
}: DependencyProps & { dependencies: DraftDependency[] }) {
  return dependencies.map((dep) => (
    <DependencyRow key={`${dep.kind}:${dep.node}`} dep={dep} {...props} />
  ));
}

function DependencyRow({
  dep,
  librarySize,
  headers,
  targetColumn,
  onUpload,
  onBuildFromColumn,
  busy,
}: DependencyProps & { dep: DraftDependency }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [column, setColumn] = useState(
    headers.includes(targetColumn) ? targetColumn : (headers[0] ?? ""),
  );
  return (
    <fieldset>
      <legend>
        {dep.title} — {dep.fulfilled ? `✓ ${librarySize} targets` : "missing"}
      </legend>
      <p>{dep.hint}</p>
      {headers.length > 0 ? (
        <p>
          <select
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
          </select>{" "}
          <button type="button" disabled={busy || !column} onClick={() => onBuildFromColumn(column)}>
            Build from this column
          </button>
        </p>
      ) : null}
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
      <button type="button" disabled={busy} onClick={() => inputRef.current?.click()}>
        {dep.fulfilled ? "Replace with a file" : "or drop a file"}
      </button>
    </fieldset>
  );
}

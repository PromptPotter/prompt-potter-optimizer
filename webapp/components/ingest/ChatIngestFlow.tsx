"use client";

import type { DatasetIndexEntry } from "@/lib/api";
import { FileDropZone } from "@/components/forms/FileDropZone";
import { BenchmarkList } from "./BenchmarkList";
import type { OnMinted } from "./types";

// ----- Empty-collection branch — drop a CSV, tune, commit -------------------

export function ChatIngestFlow({
  benchmarks,
  busy,
  uploadError,
  onFile,
  onPickDemo,
  onClose,
  onMinted,
}: {
  benchmarks: DatasetIndexEntry[];
  busy: boolean;
  uploadError: string | null;
  onFile: (file: File) => void;
  onPickDemo: (entry: DatasetIndexEntry) => void;
  onClose: () => void;
  onMinted?: OnMinted;
}) {
  return (
    <div className="new-campaign-body">
      <p>
        Drop a CSV, TSV, JSON, or Excel file to start your first dataset — any
        column names work. You&rsquo;ll pick which column is the input and which
        is the target on the next step.
      </p>
      <FileDropZone busy={busy} onFile={onFile} />
      {uploadError ? <p className="new-campaign-error">{uploadError}</p> : null}
      {benchmarks.length > 0 ? (
        <details>
          <summary>Or run a benchmark</summary>
          <BenchmarkList
            benchmarks={benchmarks}
            onClose={onClose}
            onMinted={onMinted}
            onPickDemo={onPickDemo}
            busy={busy}
          />
        </details>
      ) : null}
    </div>
  );
}

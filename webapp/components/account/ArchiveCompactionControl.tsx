"use client";
import { useState } from "react";
import { postCompactArchive } from "@/lib/api";
import type { ArchiveReport } from "@/lib/api/types";
import { useCommand, type CommandFailure } from "@/lib/hooks/useCommand";
import { fmtBytes } from "@/lib/format";
import { Button, SegmentedControl, type Segment } from "@/components/ui";

type Mode = "compact" | "restore" | "purge-cold";

const MODES: readonly Segment<Mode>[] = [
  { value: "compact", label: "Compact" },
  { value: "restore", label: "Restore" },
  { value: "purge-cold", label: "Purge" },
];

const BLURB: Record<Mode, string> = {
  compact:
    "Moves the fields nothing reads out of candidate runs into a compressed store beside them. Origin and round-parent runs are never touched — they serve almost every cache replay.",
  restore: "Puts every moved field back and drops the compressed copy.",
  "purge-cold":
    "Deletes the compressed copy for good. The rows cost real money and hours to measure again, and nothing puts them back.",
};

const describeFailure =
  (apply: boolean) =>
  (f: CommandFailure): string =>
    f.kind === "denied"
      ? "This account cannot run archive maintenance."
      : apply
        ? "The archive did not finish. Some runs may already have been rewritten — preview again to see what stands."
        : "Could not reach the archive. Nothing was changed.";

export function ArchiveCompactionControl() {
  const [mode, setMode] = useState<Mode>("compact");
  const [preview, setPreview] = useState<ArchiveReport | null>(null);
  const [done, setDone] = useState<ArchiveReport | null>(null);
  // Two slots because `revalidate` is per-slot: a dry run reaches nothing a poll reads.
  const dry = useCommand<Mode>("archive-preview", {
    revalidate: false,
    describe: describeFailure(false),
  });
  const commit = useCommand<Mode>("archive-apply", { describe: describeFailure(true) });
  const busy = dry.pending !== null || commit.pending !== null;
  const error = commit.failure?.message ?? dry.failure?.message ?? null;

  // A preview must not stand as consent for another mode.
  function pick(next: Mode) {
    setMode(next);
    setPreview(null);
    setDone(null);
    dry.clear();
    commit.clear();
  }

  async function run(apply: boolean) {
    const slot = apply ? commit : dry;
    const r = await slot.run(mode, () => postCompactArchive({ mode, apply }));
    if (!r.ok) {
      // Each run swaps atomically but the batch does not, so a failed apply voids its preview.
      if (apply) setPreview(null);
      return;
    }
    if (apply) {
      setDone(r.value);
      setPreview(null);
    } else {
      setPreview(r.value);
    }
  }

  const report = done ?? preview;
  const blocked = report !== null && report.archive_writers > 0;

  return (
    <div className="wsmaint">
      <SegmentedControl
        options={MODES}
        value={mode}
        onChange={pick}
        ariaLabel="Archive maintenance mode"
      />
      <p className="account-muted wsmaint-blurb">{BLURB[mode]}</p>

      <div className="wsmaint-actions">
        <Button onClick={() => void run(false)} disabled={busy}>
          {busy && !preview ? "Checking…" : "Preview"}
        </Button>
        <Button
          variant={mode === "purge-cold" ? "danger" : "primary"}
          onClick={() => void run(true)}
          disabled={busy || preview === null || blocked || preview.files_touched === 0}
        >
          {mode === "purge-cold" ? "Delete permanently" : "Apply"}
        </Button>
      </div>

      {error && <p className="account-error">{error}</p>}

      {report !== null &&
        report.notes.map((note) => (
          <p key={note} className={blocked ? "account-error" : "account-muted"}>
            {note}
          </p>
        ))}

      {report && !blocked && (
        <dl className="wsmaint-report">
          {/* Restore's `bytes_freed` is negative by construction — a cost, not "Freed". */}
          <div>
            <dt>
              {report.bytes_freed < 0
                ? done
                  ? "Cost"
                  : "Would cost"
                : done
                  ? "Freed"
                  : "Would free"}
            </dt>
            <dd>{fmtBytes(Math.abs(report.bytes_freed))}</dd>
          </div>
          <div>
            <dt>Files</dt>
            <dd>{report.files_touched} touched</dd>
          </div>
          <div>
            <dt>Answers</dt>
            <dd>{report.rows_moved}</dd>
          </div>
        </dl>
      )}

      {report && !blocked && report.files_touched === 0 && (
        <p className="account-muted">Nothing to do.</p>
      )}
    </div>
  );
}

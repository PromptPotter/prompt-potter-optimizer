import { useState } from "react";
import { commands } from "@/lib/api";
import { type ArchiveReport, ARCHIVE_MODE_INFO, type ArchiveMode as Mode } from "@/lib/api/types";
import { wordRows } from "@/lib/api/words";
import { useCommand } from "@/lib/hooks/useCommand";
import { fmtBytes } from "@/lib/format";
import { Button, FailureLine, Said, SegmentedControl, type Segment } from "@/components/ui";

const MODES: readonly Segment<Mode>[] = wordRows(ARCHIVE_MODE_INFO).map(([value, info]) => ({
  value,
  label: info.label,
}));

export function ArchiveCompactionControl() {
  const [mode, setMode] = useState<Mode>("compact");
  const [preview, setPreview] = useState<ArchiveReport | null>(null);
  const [done, setDone] = useState<ArchiveReport | null>(null);
  // Two slots because `revalidate` is per-slot: a dry run reaches nothing a poll reads.
  const dry = useCommand<Mode>("archive-preview", { revalidate: false });
  const commit = useCommand<Mode>("archive-apply");
  const busy = dry.pending !== null || commit.pending !== null;

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
    const r = await slot.run(mode, () => commands["compact-archive"]({ mode, apply }));
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
      <p className="account-muted wsmaint-blurb">{ARCHIVE_MODE_INFO[mode].sentence}</p>

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

      <FailureLine failure={commit.failure ?? dry.failure} />

      {report !== null &&
        report.notes.map((note) =>
          blocked ? (
            <Said key={note} tone="warn" label={note} />
          ) : (
            <p key={note} className="account-muted">
              {note}
            </p>
          ),
        )}

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

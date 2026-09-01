"use client";
import { useState } from "react";
import { postCompactArchive } from "@/lib/api";
import type { ArchiveReport } from "@/lib/api/types";
import { failureKind } from "@/lib/api/client";
import { bumpRevalidation } from "@/lib/revalidate";
import { fmtBytes } from "@/lib/format";
import { Button, SegmentedControl, type Segment } from "@/components/ui";

type Mode = "compact" | "restore" | "purge-cold";

const MODES: readonly Segment<Mode>[] = [
  { value: "compact", label: "Compact" },
  { value: "restore", label: "Restore" },
  { value: "purge-cold", label: "Purge" },
];

// What each mode does, in the operator's terms. `purge-cold` is the only one that destroys, and
// it says so rather than relying on the word "purge" to carry it.
const BLURB: Record<Mode, string> = {
  compact:
    "Moves the fields nothing reads out of candidate runs into a compressed store beside them. Origin and round-parent runs are never touched — they serve almost every cache replay.",
  restore: "Puts every moved field back and drops the compressed copy.",
  "purge-cold":
    "Deletes the compressed copy for good. The rows cost real money and hours to measure again, and nothing puts them back.",
};

// Archive maintenance. PREVIEW FIRST is the whole design: `apply` is unreachable until a dry run
// has returned, so the operator consents to a byte count they have actually seen rather than to a
// verb. The report shape is identical for both, so one renderer serves them.
export function ArchiveCompactionControl() {
  const [mode, setMode] = useState<Mode>("compact");
  const [preview, setPreview] = useState<ArchiveReport | null>(null);
  const [done, setDone] = useState<ArchiveReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A preview belongs to the mode that produced it; switching modes must not leave the old one
  // standing as consent for the new one.
  function pick(next: Mode) {
    setMode(next);
    setPreview(null);
    setDone(null);
    setError(null);
  }

  async function run(apply: boolean) {
    setBusy(true);
    setError(null);
    try {
      const report = await postCompactArchive({ mode, apply });
      if (apply) {
        setDone(report);
        setPreview(null);
        bumpRevalidation();
      } else {
        setPreview(report);
      }
    } catch (err) {
      const kind = failureKind(err);
      setError(
        kind === "denied"
          ? "This account cannot run archive maintenance."
          : "Could not reach the archive. Nothing was changed.",
      );
    } finally {
      setBusy(false);
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
          disabled={busy || preview === null || blocked || preview.runs_touched === 0}
        >
          {mode === "purge-cold" ? "Delete permanently" : "Apply"}
        </Button>
      </div>

      {error && <p className="account-error">{error}</p>}

      {blocked && (
        <p className="account-error">
          A campaign is still running, so nothing was read or written — the archive is shared and a
          rewrite could lose a row it is landing. Pause it and try again.
        </p>
      )}

      {report && !blocked && (
        <dl className="wsmaint-report">
          {/* Restore PUTS BACK, so its `bytes_freed` is negative by construction. Reporting that
              under "Freed" reads as a bug in the number rather than as the cost it is. */}
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
            <dt>Runs</dt>
            <dd>
              {report.runs_touched} touched, {report.runs_skipped} left alone
            </dd>
          </div>
          <div>
            <dt>Rows</dt>
            <dd>{report.rows_moved}</dd>
          </div>
          {report.conflicts > 0 && (
            <div>
              <dt>Refused</dt>
              <dd>
                {report.conflicts} run(s) — the stored copy no longer lines up, so nothing was put
                back rather than half of it.
              </dd>
            </div>
          )}
          {mode === "restore" && report.purged > 0 && (
            <div>
              <dt>Already dropped</dt>
              <dd>
                {report.purged} run(s) were purged on purpose — nothing to put back. They still
                measure difficulty and still serve a cache hit.
              </dd>
            </div>
          )}
        </dl>
      )}

      {report && !blocked && report.runs_touched === 0 && (
        <p className="account-muted">Nothing to do.</p>
      )}
    </div>
  );
}

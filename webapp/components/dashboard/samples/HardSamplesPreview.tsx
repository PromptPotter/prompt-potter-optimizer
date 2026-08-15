"use client";
import { useMemo, useState } from "react";
import type { DatasetItem, HardSampleOrder, HardSamplesScope, SampleSeries } from "@/lib/api";
import type { SeriesTotals } from "@/lib/hooks/useDatasetPreview";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { sampleBucket, sampleSpread, sampleWalk, type SampleBucket } from "@/lib/derivations";
import { fmtPct0 } from "@/lib/format";
import { cx } from "@/lib/cx";
import { HardSamplesTable } from "./HardSamplesTable";

// The MIDDLE rung of the hard-samples ladder — and it is a WINDOW you can slide, not
// a table. Three lines, always: where the run is, one step behind, one step ahead.
//
// The other two rungs already existed: a one-glyph-per-sample strip that says almost
// nothing, and the full virtualized table that owns a screen. What was missing is
// three lines that tell a reader where the run IS — and, the moment a row surprises
// them, one more step in either direction without opening the table.
//
// A JustLogic row is 1,500 characters of premises, so printing query text here
// rebuilds the wall the table exists to hold. These rows are ids and rates; the text
// lives on `title`.
//
// The axis is the declared scoring order while a candidate runs, and the served
// hard-sample ranking when nothing does. Both are served orderings — neither is
// re-derived here.

interface Props {
  datasetName: string | null;
  items: DatasetItem[];
  archivePerSample: Map<number, SampleSeries>;
  totals: SeriesTotals | null;
  measuredCount: number;
  unmeasuredCount: number;
  splitTest: number | null;
  // The key the server ranked the roster by, off its own echo; `null` while the read
  // is in flight. Not `order` — `sampleOrder` in this tree is the scoring WALK.
  rankedBy: HardSampleOrder | null;
  // The operator's PICK, null until they make one. Separate from `rankedBy` on
  // purpose: the control must move the instant it is clicked, while every LABEL
  // keeps naming the served order until the new rows actually land.
  rankedByPick: HardSampleOrder | null;
  onRankedByChange: (o: HardSampleOrder) => void;
  stale: boolean;
  error: string | null;
  scope: HardSamplesScope;
  onScopeChange: (s: HardSamplesScope) => void;
  // The declared scoring order off the SSE stream; null when nothing is running or a
  // reconnect has not seen a candidate start yet. Threaded in rather than subscribed
  // here — the chat already holds the one EventSource.
  sampleOrder?: number[] | null;
}

const ROWS = 3;

export function HardSamplesPreview({
  datasetName,
  items,
  archivePerSample,
  totals,
  measuredCount,
  unmeasuredCount,
  splitTest,
  rankedBy,
  rankedByPick,
  onRankedByChange,
  stale,
  error,
  scope,
  onScopeChange,
  sampleOrder = null,
}: Props) {
  const { dash, isLive } = useDashboard();
  const [showAll, setShowAll] = useState(false);

  // The RANKED roster, straight off the wire: `/preview` serves items in rank order
  // (`items[i].hard_sample_rank === i + 1`), so sorting it here would re-derive an
  // ordering the server already sent — and an ordering IS a score.
  const ranked = useMemo(() => items.map((it) => it.sample_id), [items]);

  const walk = sampleWalk(dash, sampleOrder, isLive);
  const running = walk.ids.length > 0;
  const ids = running ? walk.ids : ranked;
  // Idle there is no cursor: nobody is standing anywhere on a leaderboard.
  const cursor = running ? walk.cursor : -1;

  // Scrolled-away state, dropped the moment the axis itself changes — a new candidate
  // is a new walk, and an offset into the old one points at nothing. Render-phase
  // guarded, so the reset commits with the render that needs it.
  const [pinned, setPinned] = useState<number | null>(null);
  const [prevKey, setPrevKey] = useState(walk.walkKey);
  if (walk.walkKey !== prevKey) {
    setPrevKey(walk.walkKey);
    setPinned(null);
  }

  const spread = useMemo(
    () => sampleSpread(items.map((it) => archivePerSample.get(it.sample_id)?.mean_fitness ?? null)),
    [items, archivePerSample],
  );

  // Three facts, three sentences — a read that FAILED, a read still in FLIGHT, and a
  // roster that is genuinely empty are not the same thing, and only the last may be
  // stated as a fact about the campaign.
  if (error) {
    return (
      <p className="hsp-note" role="status">
        Couldn’t read this campaign’s samples{datasetName ? ` (${datasetName})` : ""}.
      </p>
    );
  }
  if (items.length === 0) {
    return (
      <p className="hsp-note" role="status">
        {stale ? "Loading samples…" : "No samples on this campaign’s dataset yet."}
      </p>
    );
  }

  const maxStart = Math.max(0, ids.length - ROWS);
  const follow = Math.min(maxStart, Math.max(0, cursor - 1));
  const start = pinned == null ? follow : Math.min(maxStart, Math.max(0, pinned));
  // Stepping back onto the position the run is at resumes following it — and so does
  // the ◉ in the gutter, which is the affordance for it. Both, because the implicit
  // one is invisible: a reader ten rows into the past cannot see how far they went.
  const step = (delta: number) => {
    const next = Math.min(maxStart, Math.max(0, start + delta));
    setPinned(next === follow ? null : next);
  };

  const rateOf = (id: number): { text: string; bucket: SampleBucket | null } => {
    const s = archivePerSample.get(id);
    const n = s?.measurements.length ?? 0;
    if (n === 0 || s?.mean_fitness == null) return { text: "not measured yet", bucket: null };
    return {
      text: `${fmtPct0(s.mean_fitness)} of ${n} ${n === 1 ? "try" : "tries"}`,
      bucket: sampleBucket(s.mean_fitness),
    };
  };
  const itemOf = (id: number): DatasetItem | undefined =>
    items.find((it) => it.sample_id === id);

  return (
    <div className="hsp">
      <div className="hsp-walk">
        <div className="hsp-nav">
          <NavButton dir="up" onClick={() => step(-1)} disabled={start === 0} live={running} />
          <span className="hsp-nav-line" aria-hidden="true" />
          {/* Only while the window is parked away from the run. Scrolling back by hand
              works too, but a reader who has walked ten rows into the past has no way
              to see how far they went — this is the way back that needs no counting. */}
          {pinned != null ? (
            <button
              type="button"
              className="hsp-nav-home"
              onClick={() => setPinned(null)}
              aria-label={running ? "Back to the sample being scored" : "Back to the top"}
              title={running ? "Back to the sample being scored" : "Back to the top"}
            >
              ◉
            </button>
          ) : null}
          <span className="hsp-nav-line" aria-hidden="true" />
          <NavButton
            dir="down"
            onClick={() => step(1)}
            disabled={start >= maxStart}
            live={running}
          />
        </div>

        <ol className="hsp-rows">
          {ids.slice(start, start + ROWS).map((id, i) => {
            const at = start + i;
            const rate = rateOf(id);
            const item = itemOf(id);
            return (
              <li key={`${at}:${id}`} className={cx("hsp-row", `is-${toneAt(at, cursor)}`)}>
                <span className="hsp-mark" aria-hidden="true">
                  {MARKS[toneAt(at, cursor)]}
                </span>
                <span className="hsp-id">#{String(id).padStart(3, "0")}</span>
                <span className={cx("hsp-rate", rate.bucket && `is-${rate.bucket}`)}>
                  {rate.text}
                </span>
                <span className="hsp-tag" title={item?.query}>
                  {labelAt(at, cursor)}
                </span>
              </li>
            );
          })}
        </ol>

        {/* The spread IS the disclosure control: it summarises the roster, so clicking
            it opens the roster. A separate "show all" line said the same thing and
            cost a row. */}
        <button
          type="button"
          className="hsp-spread"
          aria-expanded={showAll}
          onClick={() => setShowAll((v) => !v)}
          title={
            spread.measured > 0
              ? `${spread.measured} of ${items.length} rows measured in this scope — click for the full table`
              : `${items.length} rows — click for the full table`
          }
        >
          {spread.measured > 0 ? (
            <>
              <SpreadLine bucket="never" n={spread.never} />
              <SpreadLine bucket="partly" n={spread.partly} />
              <SpreadLine bucket="always" n={spread.always} />
            </>
          ) : (
            <span className="hsp-of">
              {items.length} rows, none measured here yet
              {totals?.mean_fitness != null ? ` · ${fmtPct0(totals.mean_fitness)} elsewhere` : ""}
            </span>
          )}
        </button>
      </div>

      {showAll ? (
        <div className="hsp-full">
          <HardSamplesTable
            perSample={
              new Map(
                [...archivePerSample].map(([sid, s]) => [
                  sid,
                  s.measurements.map((m) => ({ fitness: m.fitness, ord: m.ord })),
                ]),
              )
            }
            servedSeries={archivePerSample}
            datasetName={datasetName}
            datasetItems={items}
            datasetMeasuredCount={measuredCount}
            datasetUnmeasuredCount={unmeasuredCount}
            datasetSplitTest={splitTest}
            rankedBy={rankedBy}
            rankedByPick={rankedByPick}
            onRankedByChange={onRankedByChange}
            datasetStale={stale}
            scope={scope}
            onScopeChange={onScopeChange}
          />
        </div>
      ) : null}
    </div>
  );
}

type Tone = "done" | "now" | "next" | "rank";

const MARKS: Record<Tone, string> = { done: "✓", now: "▶", next: "·", rank: "·" };

function toneAt(at: number, cursor: number): Tone {
  if (cursor < 0) return "rank";
  return at < cursor ? "done" : at === cursor ? "now" : "next";
}

// The row's state in words — the marks are decoration over this, never the only
// carrier. Scrolled away from the cursor the wording widens ("measured" / "queued"),
// because three rows all claiming to be "just measured" would be a lie about two.
//
// Idle, the word is the row's POSITION in a roster `/preview` already served in rank
// order — reading the order it sent, not re-deriving one. The server states that number
// outright as `hard_sample_rank`; reading it instead is the better line and lands with
// the arc that serves the field, not ahead of it.
function labelAt(at: number, cursor: number): string {
  if (cursor < 0) return `rank ${at + 1}`;
  if (at === cursor) return "scoring now";
  if (at === cursor - 1) return "just measured";
  if (at < cursor) return "measured";
  if (at === cursor + 1) return "next in line";
  return "queued";
}

// One end of the window's travel. Disabled rather than hidden at the ends: a control
// that vanishes teaches nothing about why (`I3_affordance_honest`).
function NavButton({
  dir,
  onClick,
  disabled,
  live,
}: {
  dir: "up" | "down";
  onClick: () => void;
  disabled: boolean;
  live: boolean;
}) {
  const label = live
    ? dir === "up"
      ? "Earlier in the walk"
      : "Later in the walk"
    : dir === "up"
      ? "Higher-ranked rows"
      : "Lower-ranked rows";
  return (
    <button
      type="button"
      className="hsp-nav-btn"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
    >
      {dir === "up" ? "▲" : "▼"}
    </button>
  );
}

// The bucket name IS the label — `never` / `partly` / `always` read as English on
// their own, so nothing here translates them into a second vocabulary.
function SpreadLine({ bucket, n }: { bucket: SampleBucket; n: number }) {
  return (
    <span className={cx("hsp-b", `is-${bucket}`)}>
      <span className="hsp-dot" aria-hidden="true" />
      <strong>{n}</strong> {bucket}
    </span>
  );
}

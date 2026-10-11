"use client";
import { useMemo, useState } from "react";
import type { DatasetItem } from "@/lib/api";
import { useHardSamples } from "@/lib/hard-samples";
import { useCycleStream } from "@/lib/poll";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";
import { fmtDuration, fmtPct0 } from "@/lib/format";
import { cx } from "@/lib/cx";

const ROWS = 3;

type SampleBucket = Exclude<DatasetItem["hit_spread"], "unmeasured">;

export function HardSamplesPreview() {
  const { datasetName, items, measuredCount, totals, stale, error } = useHardSamples();
  const { dash, isLive } = useCycleStream();
  const [showAll, setShowAll] = useState(false);
  const ranked = useMemo(() => items.map((it) => it.sample_id), [items]);

  const walk = isLive ? (dash?.walk ?? null) : null;
  const running = walk != null && walk.ids.length > 0;
  const ids = running ? walk.ids : ranked;
  const cursor = running ? walk.cursor : -1;
  const walkKey = walk?.key ?? "";

  const [pinned, setPinned] = useState<number | null>(null);
  const [prevKey, setPrevKey] = useState(walkKey);
  if (walkKey !== prevKey) {
    setPrevKey(walkKey);
    setPinned(null);
  }

  const byId = useMemo(() => new Map(items.map((it) => [it.sample_id, it])), [items]);

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
  const step = (delta: number) => {
    const next = Math.min(maxStart, Math.max(0, start + delta));
    setPinned(next === follow ? null : next);
  };

  const rateOf = (id: number): { text: string; bucket: SampleBucket | null } => {
    const it = byId.get(id);
    if (it === undefined) return { text: "—", bucket: null };
    const n = it.n_measured;
    if (n === 0 || it.mean_fitness == null || it.hit_spread === "unmeasured") {
      return { text: "not measured yet", bucket: null };
    }
    return {
      text: `${fmtPct0(it.mean_fitness)} of ${n} ${n === 1 ? "try" : "tries"}`,
      bucket: it.hit_spread,
    };
  };
  const itemOf = (id: number): DatasetItem | undefined => byId.get(id);
  const openS = dash?.producer.open_for_s;
  const openFor = running && openS != null ? fmtDuration(openS) : null;

  return (
    <div className="hsp">
      <div className="hsp-walk">
        <div className="hsp-nav">
          <NavButton dir="up" onClick={() => step(-1)} disabled={start === 0} live={running} />
          <span className="hsp-nav-line" aria-hidden="true" />
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
                  {labelAt(at, cursor, item?.hard_sample_rank)}
                  {at === cursor && openFor ? ` · ${openFor}` : ""}
                </span>
              </li>
            );
          })}
        </ol>

        <button
          type="button"
          className="hsp-spread"
          aria-expanded={showAll}
          onClick={() => setShowAll((v) => !v)}
          title={
            measuredCount > 0
              ? `${measuredCount} of ${items.length} rows measured in this scope - click for the leaderboard`
              : `${items.length} rows - click for the leaderboard`
          }
        >
          {totals && measuredCount > 0 ? (
            <>
              <SpreadLine bucket="never" n={totals.never_hit} />
              <SpreadLine bucket="partly" n={totals.partly_hit} />
              <SpreadLine bucket="always" n={totals.always_hit} />
            </>
          ) : (
            <span className="hsp-of">
              {items.length} rows, none measured here yet
              {totals?.mean_fitness != null ? ` · ${fmtPct0(totals.mean_fitness)} elsewhere` : ""}
            </span>
          )}
        </button>
      </div>
      {showAll && (
        <div className="hsp-full">
          <MeasurementsPane preset={{ groupBy: "sample" }} />
        </div>
      )}

    </div>
  );
}

type Tone = "done" | "now" | "next" | "rank";

const MARKS: Record<Tone, string> = { done: "✓", now: "▶", next: "·", rank: "·" };

function toneAt(at: number, cursor: number): Tone {
  if (cursor < 0) return "rank";
  return at < cursor ? "done" : at === cursor ? "now" : "next";
}

// Position is only a fallback: a paged, filtered roster parts it from the served `hard_sample_rank`.
function labelAt(at: number, cursor: number, servedRank?: number): string {
  if (cursor < 0) return `rank ${servedRank ?? at + 1}`;
  if (at === cursor) return "scoring now";
  if (at === cursor - 1) return "just measured";
  if (at < cursor) return "measured";
  if (at === cursor + 1) return "next in line";
  return "queued";
}

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

function SpreadLine({ bucket, n }: { bucket: SampleBucket; n: number }) {
  return (
    <span className={cx("hsp-b", `is-${bucket}`)}>
      <span className="hsp-dot" aria-hidden="true" />
      <strong>{n}</strong> {bucket}
    </span>
  );
}

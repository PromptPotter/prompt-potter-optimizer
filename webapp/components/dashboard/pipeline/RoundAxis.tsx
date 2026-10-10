"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { useSelection } from "@/lib/SelectionContext";
import { useCycleStream } from "@/lib/poll";
import { cx } from "@/lib/cx";

export function RoundAxis({ trailing }: { trailing?: ReactNode }) {
  const { dash } = useCycleStream();
  const { round: selectedRound, setSelectionForRound } = useSelection();
  const completed = dash?.round_axis.completed ?? [];
  const liveRound = dash?.round_axis.live ?? null;
  const liveActive = liveRound != null;
  const followingLive =
    liveActive && (selectedRound == null || selectedRound === liveRound);

  // The strip draws no scrollbar, so the viewed round is brought into it; none viewed → the newest.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const strip = scrollRef.current;
    if (!strip) return;
    const tab = strip.querySelector<HTMLElement>(".round-tab.active");
    strip.scrollLeft = tab
      ? tab.offsetLeft - strip.offsetLeft - (strip.clientWidth - tab.offsetWidth) / 2
      : strip.scrollWidth;
  }, [selectedRound, liveRound, completed.length]);

  if (completed.length === 0 && !liveActive) return null;

  return (
    <div className="round-axis" role="tablist" aria-label="Rounds">
      <span className="round-axis-label">Round</span>
      <div className="round-axis-scroll" ref={scrollRef}>
        {completed.map((r) => {
          const active = selectedRound === r;
          return (
            <button
              key={r}
              type="button"
              role="tab"
              aria-selected={active}
              className={cx("round-tab", active && "active")}
              onClick={() => setSelectionForRound(r)}
              title={`Show round ${r} — its candidates, its optimizer nodes, its samples`}
            >
              {r}
            </button>
          );
        })}
        {liveActive && (
          <button
            type="button"
            role="tab"
            aria-selected={followingLive}
            className={cx("round-tab", "round-tab-live", followingLive && "active")}
            onClick={() => setSelectionForRound(null)}
            title="Follow the in-flight round"
          >
            <span className="round-tab-live-dot" aria-hidden />
            R{liveRound} · live
          </button>
        )}
      </div>
      {trailing}
    </div>
  );
}

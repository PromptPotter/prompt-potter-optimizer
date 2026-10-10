"use client";

import {
  createContext,
  Fragment,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import type { MessageItem, ThreadItem } from "@/lib/chat/thread";

function FileChip({ name, detail }: { name: string; detail: string | null }) {
  return (
    <div className="chat-msg user user-file">
      <div className="file-chip">
        <svg
          width="18"
          height="18"
          viewBox="0 0 18 18"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M14.5 7.5 8 14a3.5 3.5 0 0 1-4.95-4.95L9.5 2.6a2.4 2.4 0 0 1 3.4 3.4L6.4 12.5a1.3 1.3 0 0 1-1.83-1.83L11 4.2" />
        </svg>
        <span className="name">{name}</span>
        {detail != null && <span className="meta">· {detail}</span>}
      </div>
    </div>
  );
}

function Message({ item }: { item: MessageItem }) {
  if (item.role === "user") return <div className="chat-msg user">{item.text}</div>;
  if (item.tone === "warning") {
    return (
      <div className="chat-msg ai chat-msg-warn" role="status">
        {item.text}
      </div>
    );
  }
  if (item.tone === "error") {
    return (
      <div className="chat-msg ai chat-msg-error" role="alert">
        {item.text}
      </div>
    );
  }
  return <div className="chat-msg ai">{item.text}</div>;
}

function scrollerOf(thread: HTMLElement): HTMLElement {
  for (let el: HTMLElement | null = thread; el; el = el.parentElement) {
    const overflow = getComputedStyle(el).overflowY;
    if (overflow === "auto" || overflow === "scroll") return el;
  }
  return thread;
}

function scrollToFoot(thread: HTMLElement): void {
  const el = scrollerOf(thread);
  el.scrollTop = el.scrollHeight;
}

// Sub-pixel heights and a half-drawn row must not read as the reader having scrolled away.
const FOLLOW_SLACK_PX = 24;

interface ThreadFollow {
  // A block that grows ABOVE the tail calls this, or a still-following thread scrolls past it.
  hold: () => void;
}

const FollowContext = createContext<ThreadFollow | null>(null);

export function useThreadFollow(): ThreadFollow {
  const v = useContext(FollowContext);
  if (!v) throw new Error("useThreadFollow must be used inside <Thread>");
  return v;
}

export function Thread<R>({
  items,
  renderRun,
}: {
  items: readonly ThreadItem<R>[];
  renderRun: (run: R) => ReactNode;
}) {
  const threadRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);
  // A listener (scroll does not bubble; the scroller may be an ancestor), and BEFORE the follow effect, or the first paint misses the tail.
  useEffect(() => {
    if (!threadRef.current) return;
    const el = scrollerOf(threadRef.current);
    if (el !== threadRef.current) followRef.current = false;
    const onScroll = () => {
      followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= FOLLOW_SLACK_PX;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (threadRef.current && followRef.current) scrollToFoot(threadRef.current);
  }, [items]);

  const follow = useMemo<ThreadFollow>(
    () => ({
      hold: () => {
        followRef.current = false;
      },
    }),
    [],
  );

  return (
    <FollowContext.Provider value={follow}>
      <div className="chat-messages" aria-live="polite" ref={threadRef}>
        {items.map((item) =>
          item.kind === "message" ? (
            <Message key={item.id} item={item} />
          ) : item.kind === "file" ? (
            <FileChip key={item.id} name={item.name} detail={item.detail} />
          ) : item.kind === "run" ? (
            <Fragment key={item.id}>{renderRun(item.run)}</Fragment>
          ) : (
            <Fragment key={item.id}>{item.node}</Fragment>
          ),
        )}
      </div>
    </FollowContext.Provider>
  );
}

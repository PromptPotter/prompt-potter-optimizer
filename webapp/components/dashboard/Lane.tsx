"use client";
import { useSyncExternalStore, type ReactNode } from "react";

// Collapsible signal section. Three of these stack on the Dashboard tab
// (Now / Verdict / Why). Each lane's open/closed state persists in
// localStorage under `promptpotter.dash.lane.{id}` so a reload returns the
// operator to the layout they last left. State reads via
// `useSyncExternalStore` so the static-export build's pre-rendered HTML
// matches `defaultOpen` and hydrates to the stored value without a mismatch.

const KEY_PREFIX = "promptpotter.dash.lane.";
const subscribers = new Set<() => void>();

function subscribe(callback: () => void): () => void {
  subscribers.add(callback);
  return () => {
    subscribers.delete(callback);
  };
}

function emit(): void {
  for (const cb of subscribers) cb();
}

function readStored(id: string, defaultOpen: boolean): boolean {
  if (typeof window === "undefined") return defaultOpen;
  try {
    const v = window.localStorage.getItem(`${KEY_PREFIX}${id}`);
    if (v === "0") return false;
    if (v === "1") return true;
  } catch {
    /* localStorage may be unavailable in private mode */
  }
  return defaultOpen;
}

function writeStored(id: string, next: boolean): void {
  try {
    window.localStorage.setItem(`${KEY_PREFIX}${id}`, next ? "1" : "0");
    emit();
  } catch {
    /* ignore */
  }
}

interface Props {
  id: string;
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

export function Lane({ id, title, subtitle, defaultOpen = true, children }: Props) {
  const open = useSyncExternalStore(
    subscribe,
    () => readStored(id, defaultOpen),
    () => defaultOpen,
  );
  const toggle = () => writeStored(id, !open);

  return (
    <section
      className={`dash-lane${open ? " open" : " closed"}`}
      aria-labelledby={`lane-${id}-h`}
    >
      <button
        type="button"
        className="dash-lane-head"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={`lane-${id}-body`}
      >
        <span className="dash-lane-caret" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        <h2 id={`lane-${id}-h`} className="dash-lane-title">
          {title}
        </h2>
        {subtitle ? <span className="dash-lane-subtitle">{subtitle}</span> : null}
      </button>
      {open && (
        <div id={`lane-${id}-body`} className="dash-lane-body">
          {children}
        </div>
      )}
    </section>
  );
}

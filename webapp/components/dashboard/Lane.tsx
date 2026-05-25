"use client";
import type { ReactNode } from "react";
import { useLocalStorage } from "@/lib/useLocalStorage";

// Collapsible signal section. Two of these stack on the Dashboard tab
// (Now / Live state). Each lane's open/closed state persists in
// localStorage under `promptpotter.dash.lane.{id}` so a reload returns the
// operator to the layout they last left.

const KEY_PREFIX = "promptpotter.dash.lane.";

interface Props {
  id: string;
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

export function Lane({ id, title, subtitle, defaultOpen = true, children }: Props) {
  const [open, setOpen] = useLocalStorage<boolean>(
    `${KEY_PREFIX}${id}`,
    defaultOpen,
    { serialize: (v) => (v ? "1" : "0"), deserialize: (raw) => raw === "1" },
  );
  const toggle = () => setOpen((prev) => !prev);

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

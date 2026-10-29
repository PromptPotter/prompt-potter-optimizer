"use client";
import type { ReactNode } from "react";
import { BOOL, laneKey, useLocalStorage } from "@/lib/hooks/useLocalStorage";

interface Props {
  id: string;
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

export function Lane({ id, title, subtitle, defaultOpen = true, children }: Props) {
  const [open, setOpen] = useLocalStorage<boolean>(laneKey(id), defaultOpen, BOOL);
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

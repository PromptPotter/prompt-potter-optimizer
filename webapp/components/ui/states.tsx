// The one rendering of a read's state and of a failure: a surface never hand-rolls a branch on `status`.
// A call-site `className` places the line, never re-skins it.

import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { shownData, type ReadResult } from "@/lib/hooks/useRead";

export function Empty({
  title,
  children,
  className,
}: {
  title?: string;
  children?: ReactNode;
  className?: string | undefined;
}) {
  return (
    <p className={cx("note-empty", className)} role="status">
      {title ? <strong className="note-empty-title">{title}</strong> : null}
      {children}
    </p>
  );
}

export function Loading({
  children = "Loading…",
  className,
}: {
  children?: ReactNode;
  className?: string | undefined;
}) {
  return (
    <p className={cx("note-empty", className)} role="status">
      {children}
    </p>
  );
}

// `message` is `operatorMessage`'s sentence, carried by `ReadFailure` and `CommandFailure` alike.
export function FailureLine({
  failure,
  lead,
  className,
}: {
  failure: { message: string } | null;
  lead?: string;
  className?: string | undefined;
}) {
  if (failure === null) return null;
  return (
    <span className={cx("note-failure", className)} role="alert">
      {lead ? `${lead} ` : null}
      {failure.message}
    </span>
  );
}

// Data kept from an earlier read renders through a reload or a failure; `empty` returns null for data that is not.
export function ReadState<T>({
  read,
  subject,
  idle = null,
  empty,
  className,
  children,
}: {
  read: ReadResult<T>;
  subject: string;
  idle?: ReactNode;
  empty?: (data: T) => ReactNode;
  className?: string;
  children: (data: T) => ReactNode;
}) {
  if (read.status === "idle") return idle;
  const data = shownData(read);
  if (data !== null) return empty?.(data) ?? children(data);
  if (read.status === "failed") {
    return (
      <FailureLine failure={read.failure} lead={`Could not load ${subject}.`} className={className} />
    );
  }
  return <Loading className={className}>Reading {subject}…</Loading>;
}

// The `/login/` trailing slash matches `next.config.ts::trailingSlash`.
export function SignInPrompt({ message, className }: { message: ReactNode; className?: string }) {
  return (
    <p className={cx("note-empty", className)}>
      {message}{" "}
      <a href="/login/" className="note-empty-link">
        Sign in
      </a>
    </p>
  );
}

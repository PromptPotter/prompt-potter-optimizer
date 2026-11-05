import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { useAuth } from "@/lib/state/auth";
import {
  readResult,
  subscribe,
  type ReadDescriptor,
  type ReadFailure,
  type ReadResult,
} from "@/lib/read-cache";

export function readyData<T>(read: ReadResult<T>): T | null {
  return read.status === "ready" ? read.data : null;
}

export function shownData<T>(read: ReadResult<T>): T | null {
  if (read.status === "ready") return read.data;
  return read.status === "idle" ? null : read.kept;
}

export function failureOf<T>(read: ReadResult<T>): ReadFailure | null {
  return read.status === "failed" ? read.failure : null;
}

// What a read stands in for while it has nothing to show; null once it shows a body, a kept one
// included (a failed revalidation keeps the body it painted over).
export function readGap<T>(read: ReadResult<T>): Exclude<ReadResult<T>, { status: "ready" }> | null {
  return read.status === "ready" || shownData(read) !== null ? null : read;
}

type Untyped = ReadDescriptor<unknown>;

// The one hook on the cache: everything that varies per read is its descriptor's.
export function useRead<T, B = T>(read: ReadDescriptor<T, B> | null): ReadResult<T> {
  const { status, refresh } = useAuth();
  const live = read !== null && (!read.auth || status === "authed") ? read : null;
  const id = live?.id ?? null;

  // The subscription outlives the render: the cache reads this subscriber's newest descriptor.
  const latest = useRef(live);
  useEffect(() => {
    latest.current = live;
  });
  const listen = useCallback(
    (notify: () => void) => {
      const first = latest.current;
      if (id === null || first === null || first.id !== id) return () => {};
      const newest = (): Untyped =>
        (latest.current?.id === id ? latest.current : first) as unknown as Untyped;
      return subscribe(newest, notify);
    },
    [id],
  );
  const snapshot = useCallback(() => readResult(live), [live]);
  const result = useSyncExternalStore(listen, snapshot, snapshot);

  // A 401 re-probes the session, so one that died mid-run halts the polls.
  const lapsed = live?.auth === true && result.status === "failed" && result.failure.kind === "auth";
  useEffect(() => {
    if (lapsed) refresh();
  }, [lapsed, result, refresh]);
  return result;
}

"use client";

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type MessageTone = "plain" | "warning" | "error";

export interface MessageItem {
  id: string;
  kind: "message";
  role: "user" | "assistant";
  tone: MessageTone;
  text: string;
}

interface FileItem {
  id: string;
  kind: "file";
  name: string;
  detail: string | null;
}

// Captured VALUES of a task that ended, never a pointer to state the task's resume rewrites.
interface RunItem<R> {
  id: string;
  kind: "run";
  key: string;
  run: R;
}

export interface BlockItem {
  id: string;
  kind: "block";
  node: ReactNode;
}

export type StoredItem<R> = MessageItem | FileItem | RunItem<R>;
export type ThreadItem<R> = StoredItem<R> | BlockItem;

type Draft = Omit<MessageItem, "id"> | Omit<FileItem, "id">;

// One conversation per SUBJECT — a run's address or a check-in's — so nothing one files reaches another.
export type ThreadKey = string;

export function runThreadKey(address: string): ThreadKey {
  return `run:${address}`;
}

// A fresh check-in (no draft yet) is `null`; its thread moves under the draft id once one is minted.
export function checkinThreadKey(draftId: string | null): ThreadKey {
  return `checkin:${draftId ?? "new"}`;
}

interface Threads<R> {
  of: (key: ThreadKey) => readonly StoredItem<R>[];
  append: (key: ThreadKey, item: Draft) => string;
  setFileDetail: (key: ThreadKey, id: string, detail: string) => void;
  /** Idempotent per `runKey`: an ending can be observed twice (a re-mount, a switch back). */
  appendRun: (key: ThreadKey, runKey: string, run: R) => void;
  clear: (key: ThreadKey) => void;
  /** A fresh check-in's thread becomes its draft's, the moment the draft is minted. */
  move: (from: ThreadKey, to: ThreadKey) => void;
}

const ThreadContext = createContext<Threads<unknown> | null>(null);

type Book = Readonly<Record<ThreadKey, readonly StoredItem<unknown>[]>>;
const NONE: readonly StoredItem<unknown>[] = [];

export function ThreadProvider({ children }: { children: ReactNode }) {
  const [book, setBook] = useState<Book>({});
  const edit = useCallback(
    (key: ThreadKey, f: (m: readonly StoredItem<unknown>[]) => readonly StoredItem<unknown>[]) =>
      setBook((b) => ({ ...b, [key]: f(b[key] ?? NONE) })),
    [],
  );

  const of = useCallback((key: ThreadKey) => book[key] ?? NONE, [book]);
  const append = useCallback(
    (key: ThreadKey, item: Draft): string => {
      const id = crypto.randomUUID();
      edit(key, (m) => [...m, { ...item, id }]);
      return id;
    },
    [edit],
  );
  const setFileDetail = useCallback(
    (key: ThreadKey, id: string, detail: string) =>
      edit(key, (m) => m.map((x) => (x.id === id && x.kind === "file" ? { ...x, detail } : x))),
    [edit],
  );
  const appendRun = useCallback(
    (key: ThreadKey, runKey: string, run: unknown) =>
      edit(key, (m) =>
        m.some((x) => x.kind === "run" && x.key === runKey)
          ? m
          : [...m, { id: crypto.randomUUID(), kind: "run", key: runKey, run }],
      ),
    [edit],
  );
  const clear = useCallback((key: ThreadKey) => edit(key, () => NONE), [edit]);
  const move = useCallback(
    (from: ThreadKey, to: ThreadKey) =>
      setBook((b) => {
        const { [from]: moved = NONE, ...rest } = b;
        return { ...rest, [to]: [...(rest[to] ?? NONE), ...moved] };
      }),
    [],
  );

  const value = useMemo(
    () => ({ of, append, setFileDetail, appendRun, clear, move }),
    [of, append, setFileDetail, appendRun, clear, move],
  );
  return createElement(ThreadContext.Provider, { value }, children);
}

// `R` is the host's run payload; one app mounts one provider and names one `R`.
export function useThreads<R>(): Threads<R> {
  const v = useContext(ThreadContext);
  if (!v) throw new Error("useThreads must be used inside ThreadProvider");
  return v as Threads<R>;
}

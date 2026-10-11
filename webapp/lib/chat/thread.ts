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

export interface FileItem {
  id: string;
  kind: "file";
  name: string;
  detail: string | null;
}

// Captured VALUES of a task that ended, never a pointer to state the task's resume rewrites.
export interface RunItem<R> {
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

export interface ThreadStore<R> {
  items: readonly StoredItem<R>[];
  append: (item: Draft) => string;
  setFileDetail: (id: string, detail: string) => void;
  /** Idempotent per `key`: an ending can be observed twice (a re-mount, a switch back). */
  appendRun: (key: string, run: R) => void;
  clear: () => void;
}

const ThreadContext = createContext<ThreadStore<unknown> | null>(null);

export function ThreadProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<readonly StoredItem<unknown>[]>([]);

  const append = useCallback((item: Draft): string => {
    const id = crypto.randomUUID();
    setItems((m) => [...m, { ...item, id }]);
    return id;
  }, []);
  const setFileDetail = useCallback(
    (id: string, detail: string) =>
      setItems((m) => m.map((x) => (x.id === id && x.kind === "file" ? { ...x, detail } : x))),
    [],
  );
  const appendRun = useCallback(
    (key: string, run: unknown) =>
      setItems((m) =>
        m.some((x) => x.kind === "run" && x.key === key)
          ? m
          : [...m, { id: crypto.randomUUID(), kind: "run", key, run }],
      ),
    [],
  );
  const clear = useCallback(() => setItems([]), []);

  const value = useMemo(
    () => ({ items, append, setFileDetail, appendRun, clear }),
    [items, append, setFileDetail, appendRun, clear],
  );
  return createElement(ThreadContext.Provider, { value }, children);
}

// `R` is the host's run payload; one app mounts one provider and names one `R`.
export function useThread<R>(): ThreadStore<R> {
  const v = useContext(ThreadContext);
  if (!v) throw new Error("useThread must be used inside ThreadProvider");
  return v as ThreadStore<R>;
}

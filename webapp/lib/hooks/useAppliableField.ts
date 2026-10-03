"use client";
// Edit buffer for an "edit then Apply" field; a server-applied `value` overwrites it.

import { useState } from "react";

export function useAppliableField(value: string): {
  local: string;
  setLocal: (v: string) => void;
  dirty: boolean;
} {
  const [prev, setPrev] = useState(value);
  const [local, setLocal] = useState(value);
  if (value !== prev) {
    setPrev(value);
    setLocal(value);
  }
  return { local, setLocal, dirty: local !== value };
}

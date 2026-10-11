"use client";

import type { ReactNode } from "react";
import type { MeasuredLift, PairedReading } from "@/lib/api/types";
import { fmtLift, readPaired, type PairFacet } from "@/lib/derivations";
import { Term } from "@/components/ui";

export function PairedLift({
  reading,
  facet = "rates",
  unread = "term",
  children,
}: {
  reading: PairedReading;
  facet?: PairFacet;
  unread?: "term" | "label" | "sentence";
  children?: (lift: MeasuredLift, cells: number) => ReactNode;
}) {
  const pair = readPaired(reading);
  if (pair.read) return <>{children ? children(pair.lift, pair.cells) : fmtLift(pair.lift, facet)}</>;
  if (unread === "sentence") return <>{pair.sentence}</>;
  if (unread === "label") return <>{pair.label}</>;
  return <Term content={pair.sentence}>{pair.label}</Term>;
}

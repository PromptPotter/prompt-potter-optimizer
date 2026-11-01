"use client";
import { Fragment } from "react";
import type { LeafBytes } from "@/lib/api";
import { LEAF_INFO } from "@/lib/api/types.generated";
import { fmtBytes } from "@/lib/format";
import { Term } from "@/components/ui";

// One campaign tree on disk, wherever it is read: the served leaves in their served order, the loop's
// leaves under its subtotal.
export function LeafSplit({ bytes }: { bytes: LeafBytes }) {
  const firstLoop = bytes.leaves.find((s) => LEAF_INFO[s.leaf].loop)?.leaf;
  return (
    <dl className="leaf-split">
      {bytes.leaves.map((s) => {
        const info = LEAF_INFO[s.leaf];
        return (
          <Fragment key={s.leaf}>
            {s.leaf === firstLoop && (
              <>
                <dt>Loop</dt>
                <dd>{fmtBytes(bytes.loop)}</dd>
              </>
            )}
            <dt data-indent={info.loop || undefined}>
              <Term content={info.description}>{info.label}</Term>
            </dt>
            <dd>{fmtBytes(s.bytes)}</dd>
          </Fragment>
        );
      })}
    </dl>
  );
}

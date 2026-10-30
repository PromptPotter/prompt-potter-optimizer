import type { HeadToHead } from "@/lib/api";
import { GuardBadge } from "./GuardBadge";
import { cx } from "@/lib/cx";
import {
  sideTone,
  fmtInterval,
  fmtPValue,
  fmtUnit,
  PLACEHOLDER,
  verdictTone,
} from "@/lib/format";
import { DataTable, type Column } from "@/components/ui";

export function HeadToHeadVerdict({ h2h, scorerId }: { h2h: HeadToHead; scorerId: string }) {
  return (
    <div className="cmp-verdict">
      <p className={cx("cmp-verdict-line", verdictTone(h2h.verdict))}>
        <strong>
          Bench head-to-head{h2h.guard.head_to_head_id ? ` ${h2h.guard.head_to_head_id}` : ""}
        </strong>
        {" — "}
        {h2h.verdict_line}
      </p>
      <p className="cmp-verdict-meta">
        <span className="cmp-channel-badges" role="group" aria-label="Comparability guard">
          <GuardBadge guard={h2h.guard} />
        </span>
        <span className="l4-dim">
          graded by <code className="cmp-verdict-scorer">{scorerId}</code>
        </span>
      </p>
      {h2h.notes.length > 0 && (
        <ul className="cmp-verdict-notes">
          {h2h.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      {h2h.contrasts.length > 0 && (
        <DataTable
          columns={CONTRAST_COLUMNS}
          rows={h2h.contrasts}
          getRowId={contrastId}
          ariaLabel={`The arm × arm matrix on the bench rows every replicate scored, in ${h2h.headline}`}
        />
      )}
    </div>
  );
}

type ArmContrast = HeadToHead["contrasts"][number];
type ContrastArm = ArmContrast["a"];

const armLabel = (arm: ContrastArm) =>
  `${arm.optimizer}·${arm.treatment_digest.slice(0, 6)} ×${arm.replicates.length}`;
const contrastId = (c: ArmContrast) => `${c.a.treatment_digest}>${c.b.treatment_digest}`;

const CONTRAST_COLUMNS: readonly Column<ArmContrast>[] = [
  {
    id: "contrast",
    label: "arm b − a",
    width: "minmax(12rem, 2fr)",
    cell: ({ a, b }) => `${armLabel(a)} → ${armLabel(b)}`,
  },
  {
    id: "lift",
    label: "shift, row-clustered",
    width: "minmax(10rem, 2fr)",
    cell: ({ lift }) =>
      lift ? (
        <span className={sideTone(lift.estimate.side)}>
          {fmtUnit("delta", lift.estimate.value)}{" "}
          {fmtInterval("delta", lift.estimate.ci_lo, lift.estimate.ci_hi)}
        </span>
      ) : (
        <span className="l4-dim">under two rows</span>
      ),
  },
  {
    id: "n",
    label: "rows",
    width: "4rem",
    align: "end",
    cell: ({ differences }) => Object.keys(differences).length,
  },
  {
    id: "deff",
    label: "design effect",
    width: "6rem",
    align: "end",
    cell: ({ design_effect }) =>
      design_effect === null ? PLACEHOLDER : design_effect.toFixed(2),
  },
  {
    id: "holm",
    label: "Holm",
    width: "8rem",
    cell: ({ lift }) => fmtPValue(lift?.family?.p_adjusted ?? null),
  },
  {
    id: "bh",
    label: "BH q",
    width: "8rem",
    cell: ({ q_value }) => fmtPValue(q_value),
  },
  {
    id: "guard",
    label: "guard",
    width: "minmax(8rem, 1fr)",
    cell: ({ guard }) => (
      <span className="cmp-channel-badges">
        <GuardBadge guard={guard} />
      </span>
    ),
  },
];

import type { HeadToHead } from "@/lib/api";
import { GuardBadge } from "@/components/shell/GuardBadge";
import { PairedLift } from "@/components/shell/PairedLift";
import { readPaired } from "@/lib/derivations";
import { cx } from "@/lib/cx";
import {
  sideTone,
  fmtMetricInterval,
  fmtPValue,
  fmtSigned,
  shortId,
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
      {h2h.pairs.length > 0 && (
        <DataTable
          columns={PAIR_COLUMNS}
          rows={h2h.pairs}
          getRowId={pairId}
          ariaLabel={`Selections paired on the bench rows both scored, in ${h2h.headline}`}
        />
      )}
    </div>
  );
}

type SelectionPair = HeadToHead["pairs"][number];

const pairCampaign = (m: SelectionPair["reading"]["a"]) =>
  m?.address.path.at(-1)?.campaign_id ?? "";
const pairId = (p: SelectionPair) =>
  `${pairCampaign(p.reading.a)}>${pairCampaign(p.reading.b)}`;

const PAIR_COLUMNS: readonly Column<SelectionPair>[] = [
  {
    id: "pair",
    label: "selection b − a",
    width: "minmax(12rem, 2fr)",
    cell: ({ reading }) =>
      `${shortId(pairCampaign(reading.a))} → ${shortId(pairCampaign(reading.b))}`,
  },
  {
    id: "lift",
    label: "lift",
    width: "minmax(10rem, 2fr)",
    cell: ({ reading }) =>
      readPaired(reading).read ? (
        <PairedLift reading={reading}>
          {({ estimate }) => (
            <span className={sideTone(estimate.side)}>
              {fmtSigned(estimate.value)}{" "}
              {fmtMetricInterval("delta", estimate.ci_lo, estimate.ci_hi)}
            </span>
          )}
        </PairedLift>
      ) : (
        <span className="l4-dim">
          <PairedLift reading={reading} unread="sentence" />
        </span>
      ),
  },
  {
    id: "n",
    label: "rows",
    width: "4rem",
    align: "end",
    cell: ({ reading }) => {
      const pair = readPaired(reading);
      return pair.read ? pair.cells : "—";
    },
  },
  {
    id: "holm",
    label: "Holm",
    width: "8rem",
    cell: ({ reading }) => {
      const pair = readPaired(reading);
      return fmtPValue(pair.read ? (pair.lift.family?.p_adjusted ?? null) : null);
    },
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

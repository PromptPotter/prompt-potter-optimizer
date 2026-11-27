// The host's bulletins: one marked to pop up opens on every page load until this account
// dismisses it (closing it only waits for the next load); every one waits in the list.
import { useState } from "react";
import { ACCOUNT, bulletinsRead, commands } from "@/lib/api";
import type { ServedBulletin } from "@/lib/api/types";
import { fmtDateTime } from "@/lib/format";
import { useCommand } from "@/lib/hooks/useCommand";
import { shownData, useRead } from "@/lib/hooks/useRead";
import { Button, Dialog, FailureLine, Icon } from "@/components/ui";

function Bulletin({ bulletin }: { bulletin: ServedBulletin }) {
  return (
    <div className="bulletin">
      <time className="bulletin-when" dateTime={bulletin.posted_at}>
        {fmtDateTime(bulletin.posted_at)}
      </time>
      <p className="bulletin-text">{bulletin.text}</p>
    </div>
  );
}

export function Bulletins() {
  const read = useRead(bulletinsRead());
  const cmd = useCommand<"dismiss-bulletin">("bulletins", ACCOUNT);
  const [listOpen, setListOpen] = useState(false);
  const [closed, setClosed] = useState<ReadonlySet<string>>(new Set());

  const bulletins = shownData(read)?.bulletins;
  if (!bulletins?.length) return null;
  const unread = bulletins.filter((b) => !b.dismissed);
  const popup = listOpen ? undefined : unread.find((b) => b.pops_up && !closed.has(b.id));
  const dismiss = (id: string) =>
    void cmd.run("dismiss-bulletin", () => commands["dismiss-bulletin"]({ bulletin_id: id }));
  const dismissButton = (b: ServedBulletin) => (
    <Button variant="primary" disabled={cmd.pending !== null} onClick={() => dismiss(b.id)}>
      {cmd.pending !== null ? "Dismissing…" : "Dismiss"}
    </Button>
  );

  return (
    <>
      <button
        type="button"
        className="bulletins-trigger"
        aria-label={`Bulletins from the host, ${unread.length} not dismissed`}
        onClick={() => setListOpen(true)}
      >
        <Icon size={16} viewBox="0 0 16 16" strokeWidth={1.4}>
          <path d="M2 6.5v3h2.5L10 13V3L4.5 6.5H2zM12 6a2.5 2.5 0 0 1 0 4" />
        </Icon>
        {unread.length ? <span className="bulletins-count">{unread.length}</span> : null}
      </button>
      {popup ? (
        <Dialog
          open
          title="Bulletin from the host"
          onClose={() => setClosed(new Set([...closed, popup.id]))}
          footer={dismissButton(popup)}
        >
          <Bulletin bulletin={popup} />
          <FailureLine failure={cmd.failure} />
        </Dialog>
      ) : null}
      <Dialog open={listOpen} title="Bulletins from the host" onClose={() => setListOpen(false)}>
        <ul className="bulletins-list">
          {bulletins.map((b) => (
            <li key={b.id}>
              <Bulletin bulletin={b} />
              {b.dismissed ? <span className="bulletins-dismissed">Dismissed</span> : dismissButton(b)}
            </li>
          ))}
        </ul>
        <FailureLine failure={cmd.failure} />
      </Dialog>
    </>
  );
}

"use client";
import { useRead } from "@/lib/hooks/useRead";
import { workspaceStorageRead, type WorkspaceStorageResponse } from "@/lib/api";
import { cssPct, fmtBytes } from "@/lib/format";
import { cx } from "@/lib/cx";
import { Empty, ReadState } from "@/components/ui";
import { AccountSection } from "./AccountSection";
import { ArchiveCompactionControl } from "./ArchiveCompactionControl";

export function WorkspaceStoragePanel() {
  const read = useRead(workspaceStorageRead());
  return (
    <ReadState read={read} subject="workspace storage">
      {(data) => <WorkspaceStorage data={data} />}
    </ReadState>
  );
}

function WorkspaceStorage({ data }: { data: WorkspaceStorageResponse }) {
  const max = data.campaigns.reduce((m, c) => Math.max(m, c.on_disk_bytes), 0) || 1;
  const n = data.campaigns.length;

  return (
    <>
      <AccountSection
        title="On disk"
        lede="Campaign trees, fattest first, then the archive home and everything else. The parts you own sum to the total."
        aside={
          <span className="account-figure">
            {fmtBytes(data.total_bytes)}
            <span className="account-figure-of">
              {" "}
              across {n} campaign{n === 1 ? "" : "s"}
            </span>
          </span>
        }
      >
        {n === 0 ? (
          <Empty title="No campaigns yet">
            Each campaign&rsquo;s tree lands here once one is started, so you can see which one
            the disk went to.
          </Empty>
        ) : null}
        <ul className="wstorage-list">
          {data.campaigns.map((c) => (
            <li key={c.campaign_id} className="wstorage-item">
              <div className="wstorage-item-head">
                <span className="wstorage-name" title={c.campaign_id}>
                  {c.dataset_name}
                  {c.lifecycle_status !== "active" && (
                    <span className={cx("wstorage-tag", c.lifecycle_status)}>
                      {c.lifecycle_status}
                    </span>
                  )}
                </span>
                <span className="wstorage-bytes">{fmtBytes(c.on_disk_bytes)}</span>
              </div>
              <div className="wstorage-bar" aria-hidden="true">
                <div
                  className="wstorage-bar-fill"
                  style={{ width: cssPct(Math.max(0.02, c.on_disk_bytes / max)) }}
                />
              </div>
            </li>
          ))}
          <li className="wstorage-item wstorage-aux">
            <div className="wstorage-item-head">
              <span className="wstorage-name">
                Archive home
                <span className="account-note">
                  {" "}
                  — <code>{data.archive_home_id}</code>
                  {data.archive_home_owner === "tenant"
                    ? ": your measurements; survive a campaign delete"
                    : ": shared by every account on the install; outside your total"}
                </span>
              </span>
              <span className="wstorage-bytes">{fmtBytes(data.archive_home_bytes)}</span>
            </div>
          </li>
          <li className="wstorage-item wstorage-aux">
            <div className="wstorage-item-head">
              <span className="wstorage-name">Other</span>
              <span className="wstorage-bytes">{fmtBytes(data.other_bytes)}</span>
            </div>
          </li>
        </ul>
      </AccountSection>
      <AccountSection
        title="Archive maintenance"
        lede="Preview first: nothing is written until a dry run has shown what would move."
      >
        <ArchiveCompactionControl />
      </AccountSection>
    </>
  );
}

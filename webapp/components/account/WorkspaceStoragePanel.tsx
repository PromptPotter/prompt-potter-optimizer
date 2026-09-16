"use client";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchWorkspaceStorage } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { cx } from "@/lib/cx";
import { AccountEmpty, AccountFailure, AccountLoading, AccountSection } from "./AccountSection";
import { ArchiveCompactionControl } from "./ArchiveCompactionControl";

// Workspace-wide storage rollup — per-campaign on-disk totals, fattest first, plus
// the shared caches and a residual "Other" line so the parts sum to the real total.
// Answers "where did the bucket sizes go?". Self-fetches `GET /workspace/storage`.
export function WorkspaceStoragePanel() {
  const { data, error, kind } = useFetch((signal) => fetchWorkspaceStorage(signal), []);

  if (error) return <AccountFailure kind={kind} subject="workspace storage" />;
  if (!data) return <AccountLoading subject="workspace storage" />;

  const max = data.campaigns.reduce((m, c) => Math.max(m, c.on_disk_bytes), 0) || 1;
  const n = data.campaigns.length;

  return (
    <>
      <AccountSection
        title="On disk"
        lede="Campaign trees, fattest first, then the shared reuse cache and everything else. The parts sum to the total."
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
          <AccountEmpty title="No campaigns yet">
            Each campaign&rsquo;s tree lands here once one is started, so you can see which one
            the disk went to.
          </AccountEmpty>
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
                  style={{ width: `${Math.max(2, (c.on_disk_bytes / max) * 100)}%` }}
                />
              </div>
            </li>
          ))}
          <li className="wstorage-item wstorage-aux">
            <div className="wstorage-item-head">
              <span className="wstorage-name">
                Shared cache
                <span className="account-note"> — measurements; survives a campaign delete</span>
              </span>
              <span className="wstorage-bytes">{fmtBytes(data.shared_cache_bytes)}</span>
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

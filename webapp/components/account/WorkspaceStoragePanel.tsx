"use client";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchWorkspaceStorage } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { cx } from "@/lib/cx";
import { ArchiveCompactionControl } from "./ArchiveCompactionControl";

// Workspace-wide storage rollup — per-campaign on-disk totals, fattest first, plus
// the shared caches and a residual "Other" line so the parts sum to the real total.
// Answers "where did the bucket sizes go?". Self-fetches `GET /workspace/storage`.
export function WorkspaceStoragePanel() {
  const { data, error } = useFetch((signal) => fetchWorkspaceStorage(signal), []);

  if (error) return <p className="account-error">{error}</p>;
  if (!data) return <p className="account-loading">Loading…</p>;

  const max = data.campaigns.reduce((m, c) => Math.max(m, c.on_disk_bytes), 0) || 1;

  return (
    <div className="wstorage">
      <div className="account-row">
        <span className="account-label">Total on disk</span>
        <div className="account-row-main">
          <strong className="wstorage-total">{fmtBytes(data.total_bytes)}</strong>
          <span className="account-muted"> across {data.campaigns.length} campaign(s)</span>
        </div>
      </div>
      <p className="account-muted wstorage-note">
        Per-campaign trees, fattest first, then the shared reuse cache (measurements,
        survives a campaign delete) and everything else — together they sum to the total.
      </p>
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
        {data.campaigns.length === 0 && (
          <li className="account-muted">No campaigns yet.</li>
        )}
        <li className="wstorage-item wstorage-aux">
          <div className="wstorage-item-head">
            <span className="wstorage-name">Shared cache</span>
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
      <ArchiveCompactionControl />
    </div>
  );
}

"use client";

// The dropped-file bubble in the chat thread — "filename.csv · N rows". Reuses
// the existing `.chat-msg.user-file` / `.file-chip` styles (app/styles/domains/
// chat.css). `rows` is null until the upload resolves (n_samples comes back from
// `postIngestDataset`), so the row count appears once the file is parsed.
export function ChatFileChip({ name, rows }: { name: string; rows: number | null }) {
  return (
    <div className="chat-msg user user-file">
      <div className="file-chip">
        <svg
          width="18"
          height="18"
          viewBox="0 0 18 18"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M14.5 7.5 8 14a3.5 3.5 0 0 1-4.95-4.95L9.5 2.6a2.4 2.4 0 0 1 3.4 3.4L6.4 12.5a1.3 1.3 0 0 1-1.83-1.83L11 4.2" />
        </svg>
        <span className="name">{name}</span>
        {rows != null && <span className="meta">· {rows} rows</span>}
      </div>
    </div>
  );
}

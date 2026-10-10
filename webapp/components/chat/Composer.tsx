"use client";

import { useRef, useState, type ReactNode } from "react";
import { cx } from "@/lib/cx";

export function Composer({
  value,
  onChange,
  onSubmit,
  canSend,
  placeholder,
  onFile,
  accept,
  attachLabel,
  attachDisabled,
  tools,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  canSend: boolean;
  // Must fit ONE line beside attach and send at 390px.
  placeholder: string;
  onFile: (file: File) => void;
  accept: string;
  attachLabel: string;
  attachDisabled: boolean;
  tools?: ReactNode;
}) {
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div
      className={cx("chat-input-row", dragging && "is-dragover")}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const f = e.dataTransfer.files[0];
        if (f) onFile(f);
      }}
    >
      <input
        ref={fileInputRef}
        type="file"
        hidden
        accept={accept}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
      <button
        className="chat-attach"
        type="button"
        title={attachLabel}
        aria-label={attachLabel}
        disabled={attachDisabled}
        onClick={() => fileInputRef.current?.click()}
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14.5 7.5 8 14a3.5 3.5 0 0 1-4.95-4.95L9.5 2.6a2.4 2.4 0 0 1 3.4 3.4L6.4 12.5a1.3 1.3 0 0 1-1.83-1.83L11 4.2" />
        </svg>
      </button>
      <div className="chat-field">
        <textarea
          className="chat-input"
          placeholder={placeholder}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (canSend && e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSubmit();
            }
          }}
          disabled={!canSend}
          aria-label="Chat input"
        />
        {tools}
      </div>
      <button
        className="chat-send"
        type="button"
        disabled={!canSend || !value.trim()}
        onClick={onSubmit}
      >
        Send
      </button>
    </div>
  );
}

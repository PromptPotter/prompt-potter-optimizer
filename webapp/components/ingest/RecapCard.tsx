"use client";

export function RecapCard({ text }: { text: string }) {
  return (
    <div className="origin-recap" role="status">
      <span className="origin-recap-tick" aria-hidden="true">
        ✓
      </span>
      <p>{text}</p>
    </div>
  );
}

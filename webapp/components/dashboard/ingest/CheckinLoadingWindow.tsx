"use client";

import { useEffect, useState } from "react";

// Inline "check-in agent working" line — 🤖 + the model + a seconds counter.
// Shown while the real resolve runs and during the demo's simulation.
export function CheckinLoadingWindow({ model }: { model: string }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <p className="checkin-loading" role="status" aria-live="polite">
      <span className="checkin-loading-bot" aria-hidden="true">
        🤖
      </span>
      Check-in agent setting up · <span className="checkin-loading-model">{model}</span> · {secs}s
    </p>
  );
}

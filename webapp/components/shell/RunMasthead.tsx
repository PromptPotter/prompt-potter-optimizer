"use client";
import { CyclePicker } from "@/components/shell/CyclePicker";
import { useWorkspace } from "@/lib/workspace";

// The unit's masthead — the picker styled as the title line, the leaf cycle id
// beneath in mono. ONE header shared by the Chat and Dashboard tabs (chrome),
// so the two cannot grow different answers to "what am I looking at". Centered
// the chat-column way — max-width + auto margins inside the main column — the
// one centering method both tabs now use (cycle-picker.css).
export function RunMasthead() {
  const { leafCycleId } = useWorkspace();
  return (
    <header className="run-header">
      <div className="run-title">
        <CyclePicker />
      </div>
      {leafCycleId ? <span className="run-id">ID: {leafCycleId}</span> : null}
    </header>
  );
}

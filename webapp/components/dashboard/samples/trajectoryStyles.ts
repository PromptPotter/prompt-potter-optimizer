// Shared inline styles for the Sample Trajectory views (Delta + Series grid +
// hover popup + legend). Kept together so the cell semantics (drop/add/
// new/gained/lost/kept/absent) read as one palette.

import type { CSSProperties } from "react";

// Fixed accent for the "newly added" / "first appearance" semantic in the
// Series view. Distinct from success/danger so add ≠ gained-position.
export const NEW_COLOR = "#3b82f6";
export const NEW_BG = "rgba(59, 130, 246, 0.14)";
export const NEW_BORDER = "rgba(59, 130, 246, 0.42)";

export const SQ_BASE: CSSProperties = {
  width: 26,
  height: 26,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  border: "0.5px solid var(--color-border-tertiary)",
  borderRadius: 2,
  background: "var(--color-background-secondary)",
  color: "var(--color-text-secondary)",
  flexShrink: 0,
};

export const SQ_DROP: CSSProperties = {
  ...SQ_BASE,
  color: "var(--color-danger)",
  borderColor: "var(--color-danger-border)",
  background: "var(--color-danger-bg)",
};

export const SQ_ADD: CSSProperties = {
  ...SQ_BASE,
  color: "var(--color-success)",
  borderColor: "var(--color-success-border)",
  background: "var(--color-success-bg)",
};

export const SQ_NEW: CSSProperties = {
  ...SQ_BASE,
  color: NEW_COLOR,
  borderColor: NEW_BORDER,
  background: NEW_BG,
};

export const SQ_GAINED = SQ_ADD;
export const SQ_LOST: CSSProperties = {
  ...SQ_BASE,
  color: "var(--color-warn)",
  borderColor: "var(--color-warn-border)",
  background: "var(--color-warn-bg)",
};

export const SQ_KEPT = SQ_BASE;
export const SQ_ABSENT: CSSProperties = {
  ...SQ_BASE,
  background: "var(--color-background-tertiary, #1a1a1a)",
  borderColor: "var(--color-border-secondary)",
  color: "transparent",
};

export const ROW_LABEL: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  color: "var(--color-text-tertiary)",
  width: 38,
  flexShrink: 0,
  textAlign: "right",
  paddingRight: 6,
  lineHeight: "26px",
};

export const COL_HEADER: CSSProperties = {
  ...SQ_BASE,
  border: "none",
  background: "transparent",
  color: "var(--color-text-tertiary)",
  fontSize: 9,
};

const POP_CHIP: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  padding: "1px 4px",
  borderRadius: 2,
  border: "0.5px solid var(--color-border-tertiary)",
};
export const POP_COMPUTED: CSSProperties = {
  ...POP_CHIP,
  color: "var(--color-success)",
  borderColor: "var(--color-success-border)",
  background: "var(--color-success-bg)",
};
export const POP_CURRENT: CSSProperties = {
  ...POP_CHIP,
  color: NEW_COLOR,
  borderColor: NEW_BORDER,
  background: NEW_BG,
};
export const POP_PLANNED: CSSProperties = {
  ...POP_CHIP,
  color: "var(--color-text-tertiary)",
  background: "var(--color-background-secondary)",
};

// The `@/components/workflow` surface — the optimizer canvas and the round axis
// that scopes it, plus the shared geometry (layout) and view types. The node
// DETAIL is not here: it renders for either canvas on either tab, which makes it
// chrome (`shell/node-surface/NodeDetail`).

export * from "./types";
export * from "./layout";
export * from "./RoundAxis";
export * from "./WorkflowCanvas";

// The `@/components/workflow` surface — the optimizer CARD (its frame, round axis and
// liveness) and the view types. The card draws no graph of its own: every pipeline on
// every surface goes through `dashboard/pipeline/PipelineFlow`. The node DETAIL is not
// here either — it renders for either scope on either tab, which makes it chrome
// (`shell/node-surface/NodeDetail`).

export * from "./types";
export * from "./RoundAxis";
export * from "./OptimizerCard";

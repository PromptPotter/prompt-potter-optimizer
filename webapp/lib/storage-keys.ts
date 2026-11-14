// Every key the browser stores under, one table. A viewer's stored conveniences are not survivors:
// a renamed key starts fresh, never read under its old name.
export const STORAGE_KEYS = {
  theme: "promptpotter.theme",
  diagnostics: "promptpotter.diagnostics.incidents",
  viewMemory: "promptpotter.view.byCampaign",
  sidebarCollapsed: "promptpotter.sidebar.collapsed",
  sidebarWidth: "promptpotter.sidebar.width",
  treeShowCandidates: "promptpotter.tree.showCandidates",
  chunkReloadAt: "promptpotter.chunk-reload-at",
  sidePanel: (panelId: string) => `promptpotter.side-panel.${panelId}` as const,
} as const;

type Keys = typeof STORAGE_KEYS;
export type StorageKey = { [K in keyof Keys]: Keys[K] extends (id: string) => infer R ? R : Keys[K] }[keyof Keys];

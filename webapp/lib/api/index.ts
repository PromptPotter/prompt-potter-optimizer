// The `@/lib/api` surface — types, read endpoints, and mutating endpoints.
// `client.ts` (the API base + `jget` helper) is internal and not re-exported,
// except `ApiError` — callers branch on `err.status` (e.g. 401 → needs-auth).

export { ApiError } from "./client";
export * from "./types";
export * from "./reads";
export * from "./mutations";

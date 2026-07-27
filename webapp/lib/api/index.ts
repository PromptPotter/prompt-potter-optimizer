// The `@/lib/api` surface — types, read endpoints, and mutating endpoints.
// `client.ts` (the API base + `jget` helper) is internal and not re-exported,
// except the failure vocabulary: callers branch on `failureKind(err)` rather
// than on a status literal, so "how do I react to this?" has one answer.

export { ApiError, failureKind, type FailureKind } from "./client";
export * from "./types";
export * from "./reads";
export * from "./mutations";

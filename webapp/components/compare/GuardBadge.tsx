import { type PairGuard, GUARD_STATE_LABELS } from "@/lib/api/types";
import { Badge, Term } from "@/components/ui";

export function GuardBadge({ guard }: { guard: PairGuard }) {
  return (
    <>
      <Term content={guard.sentence}>
        <Badge tone={guard.state === "controlled" ? undefined : "danger"}>
          {GUARD_STATE_LABELS[guard.state]}
        </Badge>
      </Term>
      {guard.differs_on.map((field) => (
        <Badge key={field} tone="danger">
          {field}
        </Badge>
      ))}
    </>
  );
}

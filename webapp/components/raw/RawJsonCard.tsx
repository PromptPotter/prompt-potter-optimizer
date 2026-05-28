import type { DashboardSnapshot } from "@/lib/poll";

interface Props {
  dash: DashboardSnapshot | null;
}

export function RawJsonCard({ dash }: Props) {
  return (
    <div className="card raw-card">
      <details>
        <summary>Raw dashboard.json</summary>
        <pre>
          {dash ? JSON.stringify(dash, null, 2) : "Waiting for first poll…"}
        </pre>
      </details>
    </div>
  );
}

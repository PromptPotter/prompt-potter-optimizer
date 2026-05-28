import type { DashboardSnapshot } from "@/lib/poll";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

interface Props {
  dash: DashboardSnapshot | null;
}

export function RawJsonCard({ dash }: Props) {
  return (
    <RotatePrompt surfaceName="The raw JSON view">
      <div className="card raw-card">
        <details>
          <summary>Raw dashboard.json</summary>
          <pre>
            {dash ? JSON.stringify(dash, null, 2) : "Waiting for first poll…"}
          </pre>
        </details>
      </div>
    </RotatePrompt>
  );
}

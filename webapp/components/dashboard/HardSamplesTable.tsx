"use client";
import { useEffect, useState } from "react";
import {
  fetchActiveDatasetName,
  fetchDatasetPreview,
  type DatasetItem,
} from "@/lib/api";

// Pull every row of the leaderboard so the resizable container can scroll
// through the full train+test list.
const MAX_FETCH = 1000;

interface Props {
  cycleId: string | null;
}

// Two-column dataset preview rendered beneath the Input/LLM/Output hero
// nodes. Mounted only when `samplesOpen` is true (state lives in ChatPane).
// The scroll container uses CSS `resize:vertical` — operator drags the
// native handle in the bottom-right corner to grow the box; rows reveal
// by scrolling, no slice math.
export function HardSamplesTable({ cycleId }: Props) {
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [datasetName, setDatasetName] = useState<string | null>(null);
  const [trainCount, setTrainCount] = useState(0);
  const [testCount, setTestCount] = useState(0);

  useEffect(() => {
    if (!cycleId) return;
    let cancelled = false;
    const ac = new AbortController();
    (async () => {
      try {
        const name = await fetchActiveDatasetName(cycleId, ac.signal);
        if (cancelled || !name) return;
        setDatasetName(name);
        const preview = await fetchDatasetPreview(name, MAX_FETCH, ac.signal);
        if (cancelled) return;
        setItems(preview.items);
        setTrainCount(preview.train_count);
        setTestCount(preview.test_count);
      } catch {
        // Silent — initial load may race with cycle-mint; user retries via reload.
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [cycleId]);

  if (items.length === 0) return null;

  const total = trainCount + testCount;
  const tag = datasetName ? `${datasetName} · ` : "";

  // Surprise → hue. 0 (always-hit, easy) = cool green; 0.5 (unknown / 50-50
  // prior) = neutral grey; 1 (always-miss, hard) = warm red. The optimizer's
  // attention should track this colour gradient.
  const surpriseStyle = (s: number): React.CSSProperties => {
    const clamped = Math.max(0, Math.min(1, s));
    // 130° (green) → 35° (amber) → 5° (red) as surprise climbs.
    const hue = 130 - clamped * 125;
    const alpha = 0.18 + Math.abs(clamped - 0.5) * 0.4;
    return { background: `hsla(${hue},70%,45%,${alpha.toFixed(3)})` };
  };

  return (
    <div className="wf-samples-zone">
      <div className="wf-samples-block">
        <div className="wf-samples-scroll">
          <table className="wf-samples-table">
            <thead>
              <tr>
                <th className="col-id">ID</th>
                <th className="col-input">Input</th>
                <th className="col-output">Output</th>
                <th className="col-tries" title="Times this sample has been tried">
                  Tries
                </th>
                <th
                  className="col-surprise"
                  title="Miss-probability for an average candidate. 0.5 = no signal yet."
                >
                  Surprise
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.sample_id}>
                  <td className="col-id">{item.sample_id}</td>
                  <td className="col-input" title={item.query}>
                    {item.query}
                  </td>
                  <td className="col-output" title={item.ground_truth}>
                    {item.ground_truth}
                  </td>
                  <td className="col-tries">{item.n_obs}</td>
                  <td className="col-surprise" style={surpriseStyle(item.surprise)}>
                    {item.surprise.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="wf-samples-footer">
          <span className="counts">
            {tag}Train {trainCount} · Test {testCount} · Total {total}
          </span>
        </div>
      </div>
    </div>
  );
}

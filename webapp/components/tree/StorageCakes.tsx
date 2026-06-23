"use client";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchStorageByDataset, type DatasetStorageEntry } from "@/lib/api";
import { fmtBytes } from "@/lib/format";

// Workspace storage "cakes" on the Files view: one donut per storage CATEGORY
// (On disk + the six MECE leaves), and within each cake the slices are DATASETS
// (every campaign of a dataset pooled, so a few slices, not dozens). A dataset
// keeps the same colour across all cakes, so you can read "which dataset owns the
// dataset-mirror bytes" vs "…the connector bytes" at a glance. Self-fetches
// `GET /workspace/storage-by-dataset`; the shared measurement cache is excluded.

// The cakes. "On disk" is the whole; the six leaves partition it — the operator axis
// is Connector / Loop / Dataset, and Loop = State + Trace + History + Reports.
const CATEGORIES = [
  { key: "total_bytes", label: "On disk" },
  { key: "dataset_bytes", label: "Dataset" },
  { key: "connector_bytes", label: "Connector" },
  { key: "state_bytes", label: "State" },
  { key: "trace_bytes", label: "Trace" },
  { key: "history_bytes", label: "History" },
  { key: "reports_bytes", label: "Reports" },
] as const;

// Per-dataset colour, assigned by rank and reused across every cake.
const PALETTE = [
  "var(--color-accent)",
  "#16a34a",
  "#d97706",
  "#9333ea",
  "#0891b2",
  "#db2777",
  "#65a30d",
  "#dc2626",
  "#7c3aed",
  "#0d9488",
];

const R = 26;
const STROKE = 11;
const C = 2 * Math.PI * R;

function Cake({
  label,
  field,
  datasets,
  colors,
}: {
  label: string;
  field: keyof DatasetStorageEntry;
  datasets: DatasetStorageEntry[];
  colors: string[];
}) {
  const vals = datasets.map((d) => Number(d[field]) || 0);
  const total = vals.reduce((a, b) => a + b, 0);
  const denom = total || 1;
  const segs = vals.map((v) => (v / denom) * C);
  const offsets = segs.map((_, i) => segs.slice(0, i).reduce((a, b) => a + b, 0));
  return (
    <figure className="cake">
      <svg viewBox="0 0 72 72" className="cake-svg" role="img" aria-label={`${label} by dataset`}>
        <circle
          cx="36"
          cy="36"
          r={R}
          fill="none"
          stroke="var(--color-background-secondary)"
          strokeWidth={STROKE}
        />
        {datasets.map((d, i) => (
          <circle
            key={d.dataset_name}
            cx="36"
            cy="36"
            r={R}
            fill="none"
            stroke={colors[i]}
            strokeWidth={STROKE}
            strokeDasharray={`${segs[i]} ${C - segs[i]}`}
            strokeDashoffset={-offsets[i]}
            transform="rotate(-90 36 36)"
          >
            <title>{`${d.dataset_name}: ${fmtBytes(vals[i])}`}</title>
          </circle>
        ))}
        <text x="36" y="34" className="cake-cat" textAnchor="middle">
          {label}
        </text>
        <text x="36" y="44" className="cake-total" textAnchor="middle">
          {fmtBytes(total)}
        </text>
      </svg>
    </figure>
  );
}

export function StorageCakes() {
  const { data, error } = useFetch((signal) => fetchStorageByDataset(signal), []);
  if (error || !data || data.datasets.length === 0) return null;

  // Backend returns datasets fattest-first; pin colours by that rank so a dataset
  // is the same hue in every cake.
  const datasets = data.datasets;
  const colors = datasets.map((_, i) => PALETTE[i % PALETTE.length]);

  return (
    <div className="card cakes-card">
      <div className="cakes-head">
        <span className="cakes-title">Storage by dataset · {fmtBytes(data.total_bytes)} on disk</span>
        <ul className="cakes-legend">
          {datasets.map((d, i) => (
            <li key={d.dataset_name} title={d.dataset_name}>
              <span className="cakes-swatch" style={{ background: colors[i] }} aria-hidden="true" />
              {d.dataset_name}
            </li>
          ))}
        </ul>
      </div>
      <div className="cakes-row">
        {CATEGORIES.map((cat) => (
          <Cake
            key={cat.key}
            label={cat.label}
            field={cat.key}
            datasets={datasets}
            colors={colors}
          />
        ))}
      </div>
    </div>
  );
}

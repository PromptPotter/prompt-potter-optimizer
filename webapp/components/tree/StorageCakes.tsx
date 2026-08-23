"use client";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchStorageByDataset, type DatasetStorageEntry } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { seriesColor, useThemeVersion } from "@/lib/theme";

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

const R = 26;
const STROKE = 11;
const C = 2 * Math.PI * R;

function Cake({
  label,
  field,
  datasets,
}: {
  label: string;
  field: keyof DatasetStorageEntry;
  datasets: DatasetStorageEntry[];
}) {
  const total = datasets.reduce((a, d) => a + (Number(d[field]) || 0), 0);
  const denom = total || 1;
  const arcs = datasets.map((d, i) => {
    const value = Number(d[field]) || 0;
    return { name: d.dataset_name, value, seg: (value / denom) * C, color: seriesColor(i) };
  });
  const slices = arcs.map((a, i) => ({
    ...a,
    offset: arcs.slice(0, i).reduce((sum, x) => sum + x.seg, 0),
  }));
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
        {slices.map((s) => (
          <circle
            key={s.name}
            cx="36"
            cy="36"
            r={R}
            fill="none"
            stroke={s.color}
            strokeWidth={STROKE}
            strokeDasharray={`${s.seg} ${C - s.seg}`}
            strokeDashoffset={-s.offset}
            transform="rotate(-90 36 36)"
          >
            <title>{`${s.name}: ${fmtBytes(s.value)}`}</title>
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
  // The slices paint resolved literals, so a theme flip has to re-run this to re-read them.
  useThemeVersion();
  const { data, error } = useFetch((signal) => fetchStorageByDataset(signal), []);
  if (error || !data || data.datasets.length === 0) return null;

  // Backend returns datasets fattest-first, and colour is `seriesColor(rank)` — so a dataset is
  // the same colour in every cake, from the palette Compare and Activity also read.
  const datasets = data.datasets;

  return (
    <div className="card cakes-card">
      <div className="cakes-head">
        <span className="cakes-title">Storage by dataset · {fmtBytes(data.total_bytes)} on disk</span>
        <ul className="cakes-legend">
          {datasets.map((d, i) => (
            <li key={d.dataset_name} title={d.dataset_name}>
              <span className="cakes-swatch" style={{ background: seriesColor(i) }} aria-hidden="true" />
              {d.dataset_name}
            </li>
          ))}
        </ul>
      </div>
      <div className="cakes-row">
        {CATEGORIES.map((cat) => (
          <Cake key={cat.key} label={cat.label} field={cat.key} datasets={datasets} />
        ))}
      </div>
    </div>
  );
}

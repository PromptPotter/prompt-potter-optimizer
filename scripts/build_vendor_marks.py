"""Regenerate ``webapp/components/ui/vendor-marks.generated.ts``.

Author-time, and the OUTPUT IS COMMITTED. An icon set is a brand asset, so a mark changing shape
belongs in a diff someone reads rather than in a deploy nobody saw — which is also why the gate's
``vendor-marks`` check re-runs this and fails when the committed file has drifted.

Two sources, because neither covers the set alone. ``simple-icons`` is a devDependency, so its
version is pinned in the lock and its geometry is reviewed on upgrade like any other; it ships
plain ``icons/<slug>.svg``, read here directly. But as of v16 it carries no OpenAI, xAI, Zhipu or
Upstage mark, and those four live as files under ``webapp/assets/vendor-marks/`` — see that
directory's README for provenance, licence and why they are files rather than a second package.

Nothing is fetched at runtime and nothing is fetched here: OpenRouter's API serves no logo, and its
undocumented static icon path answers a miss with HTTP 200 ``text/html``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ASSETS = _REPO / "webapp" / "assets" / "vendor-marks"
_SIMPLE_ICONS = _REPO / "webapp" / "node_modules" / "simple-icons" / "icons"
_DEST = _REPO / "webapp" / "components" / "ui" / "vendor-marks.generated.ts"

# The vendor namespace `lib/format.ts::vendorOf` reads off a model id → how it reads, how it is
# inked, and where its geometry comes from.
#
# `si` names a simple-icons slug; otherwise the mark is `webapp/assets/vendor-marks/<vendor>.svg`
# when that file exists, and the vendor wears its INITIAL when neither does. The initial is a
# resting state, not a gap: the set of vendors is open and always will be.
#
# `tint` is declared here rather than taken from the upstream's own hex, and every value is one
# decision: a brand whose ink is black disappears into the dark surface, and two brands landing on
# the same blue stop being countable. A column of marks is read on ink first and shape second.
VENDORS: list[dict[str, str]] = [
    {"vendor": "openai", "label": "OpenAI", "tint": "#10a37f"},
    {"vendor": "anthropic", "label": "Anthropic", "tint": "#d97757", "si": "anthropic"},
    {"vendor": "google", "label": "Google", "tint": "#4285f4", "si": "googlegemini"},
    {"vendor": "deepseek", "label": "DeepSeek", "tint": "#4d6bfe", "si": "deepseek"},
    {"vendor": "qwen", "label": "Qwen", "tint": "#7c5cff", "si": "qwen"},
    {"vendor": "meta-llama", "label": "Meta", "tint": "#0084ff", "si": "meta"},
    {"vendor": "mistralai", "label": "Mistral", "tint": "#fa520f", "si": "mistralai"},
    {"vendor": "x-ai", "label": "xAI", "tint": "#9aa4b2"},
    {"vendor": "moonshotai", "label": "Moonshot", "tint": "#5b5bd6", "si": "moonshotai"},
    {"vendor": "z-ai", "label": "Z.ai", "tint": "#0ea5e9"},
    {"vendor": "nvidia", "label": "NVIDIA", "tint": "#76b900", "si": "nvidia"},
    {"vendor": "upstage", "label": "Upstage", "tint": "#e11d48"},
    # No mark in either source. `inception` and `inclusionai` share an initial, so their inks are
    # deliberately far apart — an initial still has to be countable.
    {"vendor": "microsoft", "label": "Microsoft", "tint": "#00a4ef"},
    {"vendor": "inception", "label": "Inception", "tint": "#00b3a4"},
    {"vendor": "inclusionai", "label": "InclusionAI", "tint": "#c2410c"},
]

_VIEWBOX = re.compile(r'viewBox="([^"]+)"')
_FILL = re.compile(r'fill="([^"]+)"')
_PATH_D = re.compile(r'<path[^>]*\sd="([^"]+)"')


def _paths(svg_path: Path) -> list[str]:
    """The 24x24 geometry in one file, refusing anything the component cannot ink from one place.

    A multi-path mark is fine — that is a drawing detail. A mark carrying its OWN fills is not: it
    would render as the silhouette of a logo rather than the logo.
    """
    svg = svg_path.read_text(encoding="utf-8")
    box = _VIEWBOX.search(svg)
    if not box or box.group(1) != "0 0 24 24":
        raise SystemExit(f"{svg_path.name}: viewBox {box and box.group(1)!r}, expected '0 0 24 24'")
    own = {f for f in _FILL.findall(svg) if f not in ("currentColor", "none")}
    if own:
        raise SystemExit(f"{svg_path.name}: carries its own fills ({', '.join(sorted(own))})")
    found = _PATH_D.findall(svg)
    if not found:
        raise SystemExit(f"{svg_path.name}: no path data")
    return found


def _ts(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> int:
    rows: list[str] = []
    drawn = 0
    for entry in VENDORS:
        vendor, label, tint = entry["vendor"], entry["label"], entry["tint"]
        asset = _ASSETS / f"{vendor}.svg"
        slug = entry.get("si")
        if asset.exists():
            paths, source = _paths(asset), f"webapp/assets/vendor-marks/{vendor}.svg"
        elif slug:
            icon = _SIMPLE_ICONS / f"{slug}.svg"
            if not icon.exists():
                raise SystemExit(
                    f"simple-icons has no {slug!r} — it may have dropped the mark. Check the slug, "
                    f"or add webapp/assets/vendor-marks/{vendor}.svg and drop the `si` key."
                )
            paths, source = _paths(icon), f"simple-icons/{slug}"
        else:
            paths, source = [], "no mark in either source — wears its initial"
        key = vendor if re.fullmatch(r"[a-z][a-z0-9]*", vendor) else _ts(vendor)
        geometry = f", paths: [{', '.join(_ts(p) for p in paths)}]" if paths else ""
        rows.append(
            f"  // {source}\n  {key}: {{ label: {_ts(label)}, tint: {_ts(tint)}{geometry} }},"
        )
        drawn += bool(paths)

    body = "\n".join(rows)
    _DEST.write_text(
        f"""// GENERATED by scripts/build_vendor_marks.py — do not hand-edit.
//
// Model-vendor brand marks, keyed by the namespace `lib/format.ts::vendorOf` reads off a model id.
// The comment above each row says where its geometry came from: a `simple-icons` slug read out of
// node_modules, a checked-in SVG under `webapp/assets/vendor-marks/` (that directory's README has
// provenance and licence), or neither — in which case the vendor wears its initial, which is a
// resting state rather than a gap.
//
// To change one: edit `VENDORS` in the generator, or the SVG, then re-run it. The gate's
// `vendor-marks` check fails when this file and the generator disagree.
//
// These marks are the trademarks of their owners, used to identify whose model a campaign ran on.

export interface VendorMark {{
  label: string;
  // Brand ink — a LITERAL, because third-party brand colours are not this product's palette and no
  // token could name them. Chosen to carry on both themes AND to stay apart from each other.
  tint: string;
  // The 24x24 geometry, drawn as one `<symbol>`. Several entries are one mark in several strokes,
  // every one of them `currentColor`. Absent ⇒ this vendor renders as its initial.
  paths?: readonly string[];
}}

export const VENDOR_MARKS: Record<string, VendorMark> = {{
{body}
}};
""",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"vendor-marks.generated.ts: {len(VENDORS)} vendors, {drawn} marks, {len(VENDORS) - drawn} initials"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
